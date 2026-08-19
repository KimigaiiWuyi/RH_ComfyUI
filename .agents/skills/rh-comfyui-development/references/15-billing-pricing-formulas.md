# 十五、积分计价规则总览(开发者维护手册)

> 本章是**计费规则的速查表 + 公式推导**。每个模型的计费曲线、改价流程、
> 历史 bug 都集中在这里。改积分前必读 —— 不读公式直接拍脑袋改 `point_cost`
> 会导致预览/实际扣费不一致。

## 15.1 计费三件套(必须先理解)

每个模型类继承 `AIGCGenerationBase` 后必须实现/可实现三个钩子:

| 钩子 | 何时调用 | 用途 |
|---|---|---|
| `estimate_cost(request)` | dispatch 阶段 ③ (预扣) + 调用方 `/models/estimate` | **动态计费**核心:按参数算预扣积分 |
| `settle_cost(request, usage)` | dispatch 成功后 | 供应商实扣;None=维持预扣。框架只做差额 |
| `point_cost` (静态属性) | 兜底 / `is_dynamic=false` 时使用 | 模型默认值(estimate 失败时也用它) |
| `point_range()` (可选) | 调用方判断 `min < max` 是否调 estimate API | UI 展示最低/最高,也是触发 estimate 的开关 |

**调用路径**:

```
调用方看到 catalog.point_range.min < point_range.max
   ↓ 300ms 防抖
GET /api/RH_ComfyUI/models/estimate?model=xxx&ratio=...&image_size=...
   ↓
estimate_model_points(...) → GenerationRequest → model.estimate_cost(req)
   ↓
billing mapper (utils/mappers/<model>_billing.py)
   ↓
返回 { model, point_cost, is_dynamic, point_range, params }
```

## 15.2 计价规则分类表(按模态)

> **核心约定**:`estimate_cost(request)` 是唯一计费入口。billing mapper
> 写成纯函数(`calculate_xxx_points(...)`)便于单测,`estimate_xxx_points(...)`
> 是薄壳供 estimate_cost 调用。所有数值常量集中在 mapper 文件顶部。

### 15.2.1 图片 — 按 quality + 输出像素面积分档

**代表模型**:`gpt-image-2`

**公式**(取自上游公开网关):

```
quality_axis_factor = {"low": 16, "medium": 48, "high": 96}
short_axis_factor  = (2 * quality_axis_factor * short_edge + long_edge) // (2 * long_edge)
tokens             = (quality_axis_factor * short_axis_factor * (offset + w*h) + scale - 1) // scale
                    其中 offset = 2_000_000, scale = 4_000_000
points             = (tokens * 21_000 + 999_999) // 1_000_000
                    (1 元 = 100 积分,210 元 / M tokens → 21_000 积分 / M tokens)
```

**像素尺寸映射 `_RATIO_SIZE_MAP`**(详见 `utils/mappers/gpt_image2_billing.py:48-69`):

| ratio × tier | 1K | 2K | 4K |
|---|---|---|---|
| 1:1 | 1024x1024 | 2048x2048 | 2880x2880 |
| 16:9 | 1792x1008 | 2048x1152 | 3840x2160 |
| 9:16 | 1008x1792 | 1152x2048 | 2160x3840 |
| 4:3 | 1280x960 | 2048x1536 | 3264x2448 |
| 3:4 | 960x1280 | 1536x2048 | 2448x3264 |
| 3:2 | 1536x1024 | 3072x2048 | 3456x2304 |
| 2:3 | 1024x1536 | 2048x3072 | 2304x3456 |
| 21:9 | 2464x1056 | 2688x1152 | 3808x1632 |

**硬约束**(上游 OpenAI 网关强制):
- 两边都 16 整除
- max edge ≤ 3840px
- 比例精确且 ≤ 3:1
- 像素 ∈ [655_360, 8_294_400]

**回归测试**:`tests/test_ratio_size_map_correctness.py` 校验每个 cell 满足 4 条约束、单调性。

### 15.2.2 图片 — 按 image_size 档位固定 token + 输入图数

**代表模型**:`seedream5_pro`、`banana_pro`、`banana1`、`banana2`

**公式**:

```
输入图积分 = max(num_input_images - 1, 0) × INPUT_COST_PER_IMAGE_POINTS
            (seedream5_pro: 首张免费,第 2 张起 +2 积分/张)
            (banana_pro:   每张 0.0011 美元 ≈ 0.11 积分,向上取整,最小 1 积分)

输出图积分 = (image_size → 固定 tokens) × POINTS_PER_MILLION_TOKENS / 1_000_000
            (banana_pro: 1K=1120, 2K=1120, 4K=2000 tokens → 14/14/24 积分)
            (seedream5_pro: 1K=30, 2K=60 积分,按 ≤236万 vs >236万像素分档)
```

**为什么 1K 和 2K 同价?**(常见误解)

`banana_pro` 的 `OUTPUT_TOKENS_BY_SIZE = {"1K": 1120, "2K": 1120, "4K": 2000}` 是**按官方文档**
(上游 token 计费表)固定的,不是 bug。改这个表会让本地预览偏离上游实际扣费。
`gpt-image-2` 没有这个问题,因为它按 quality_factor + 像素面积算,1K 和 2K 自动区分。

### 15.2.3 图片 — 固定积分

**代表模型**:`qwen_2512`、`anima`、`qwen_2511`、`minimax_image01`

**公式**:无 `estimate_cost` 覆盖,直接返回 `self.point_cost`(模型 `node_def()` 里设)。

**point_range**:无 `point_range()` 覆盖或返回 `(point_cost, point_cost)` → 调用方判定固定积分,不调 estimate API。

### 15.2.4 视频 — 按 token 用量(Seedance 2.x / 1.5 Pro / 1.0 Pro)

**公式**(官方):

```
tokens = (输入视频时长 + 输出视频时长) × 宽 × 高 × fps / 1024
rate   = 根据 (model, resolution, has_input_video, generate_audio) 选 rate_yuan
yuan   = round(tokens × rate_yuan / 1e6, 2)     # 对齐官方价表两位小数
points = yuan × 100                             # 1 元 = 100 积分,最小 1
```

**输入视频时长**(`resolve_input_video_duration`,详见 [§十九](./19-seedance25-and-input-duration.md)):
1. 显式 `input_video_duration`(estimate API / params)
2. 累加 video_refs 各段时长
3. 有参考但无时长 → **5s × 段数**(不是固定总 5s)
4. 无参考 → 0

**2.0 / 2.5 最低 token**:有参考视频时,输入 2~4 秒按 **4 秒** 写入公式(价表「最低价对应输入 2~4 秒」)。
4s 参考 + 4s 成片 = `(4+4)` 秒 token,不是「把 4 秒输出算成 8 秒」;无输入 4s 仍只按 4s × 无输入单价。

**金额**:`yuan = tokens × 费率 / 1e6`,先四舍五入到分,再 ×100 成积分(对齐官方价表两位小数)。

**后结算**:预扣仍用上式估算。任务成功后若 usage 带
`vendor_cost` / `completion_tokens` / `total_tokens`(Seedance 官方查询字段),
`settle_cost` 用**供应商 token × 同一档位费率**算实扣,dispatcher 只补/退差额。
RunningHub coins 等非 token 单位返回 None,维持预扣。例:ark 1080p 有输入
`total_tokens=488025` → 488025 × 31 元/M = 15.13 元 = **1513** 积分。

**关键参数**:
- `resolution` ∈ {480p, 720p, 1080p, 4k}(2.5 为 480p/720p/1080p;480p 按 854×480)
- `duration`: 输出秒;2.0 为 4~15;2.5 为 4~30 或 **-1**
- `input_video_duration` / `video_refs`:输入总时长 + 有/无输入费率档
- `generate_audio`(仅 1.5 Pro):有声 16 元/M,无声 8 元/M

**模型细分**:
| 模型 | 后端 channel | duration | 费率(无输入/有输入 元/M) |
|---|---|---|---|
| seedance2 | ark / runninghub | 4~15s | 480/720:46/28;1080:51/31;4k:26/16 |
| **seedance2.5** | **ark + gateway** | **4~30s 或 -1** | **480/720:70/42;1080:77/46** |
| seedance15_pro | ark | 4~12s | 有声 16 / 无声 8 |
| seedance2_mini / _fast | 外部/ark | 4~15s | mini 23/14;fast 37/22 |

**2.5 官方例**:
- 720p 5s 无输入 = 108000 tokens × 70 元/M = **7.56 元 = 756 积分**
- 720p 5s 输出 + 2~4s 输入 = **8.16 元 = 816 积分**
- 720p 5s 输出 + 30s 输入 = **31.75 元 = 3175 积分**
- 720p 4s 输出 + 4s 输入 = 8s token × 42 元/M = **7.26 元 = 726 积分**

### 15.2.4b 视频 — 按秒计价(MiniMax H3)

**代表模型**:`minimax_h3`

**公式**(官方按量人民币):

```
seconds = 输出秒数 + 输入参考视频秒数
rate    = 768P → 0.50 元/秒; 2K → 0.80 元/秒
points  = ceil(seconds × rate × 100)   # 1 元 = 100 积分,最小 1
```

时长 4~15 秒。须在 `MiniMax_Enabled_Models` 勾选 `minimax_h3`。

### 15.2.5 视频 — 按输出时长(Wan2.2)

**公式**:`points = max(int(duration * 60), 1)` —— 0.6 元/秒 = 60 积分/秒。

### 15.2.6 语音 — 按 UTF-8 字节数

**代表模型**:`fish_tts`、`IndexTTS2`、`mimo_tts`

**公式**:`points = (bytes × POINTS_PER_MILLION_BYTES + 999_999) // 1_000_000`,最小 1 积分。

| 模型 | 费率(美元/M bytes) | 积分/M bytes |
|---|---|---|
| fish_tts | 15 | 1500 |
| IndexTTS2 | 5 | 500 |
| mimo_tts | 6 | 600 |

**point_range 配置铁律**:max 必须用**足够长**的文本(推荐 5000 字符)算,否则
费率太低的模型(500 积分/M bytes)算不出差异,`min == max` → 调用方误判固定积分
不调 estimate API。详见 §15.4 bug #3。

### 15.2.7 语音 — 按字符数(中文)

**代表模型**:`minimax_t2a_speech`(speech-2.8-hd 系列)

**公式**:`points = (char_count × 350 + 9_999) // 10_000`,即 3.5 元/万字符,最小 1 积分。

### 15.2.8 语音识别 — 按输入音频时长

**代表模型**:`fish_asr`

**公式**:按音频时长(秒)折算,具体见 `utils/mappers/fishaudio_asr_billing.py`。

### 15.2.9 音乐 — 固定积分

**代表模型**:`ace_step1.5`

**公式**:无 `estimate_cost`,直接 `point_cost`(默认 5 积分)。

## 15.3 改价流程(必读)

### 场景 A:整模型改单价

1. 改 `node_def()` 里的 `point_cost`(**仅作静态兜底**)。
2. 改 `estimate_cost()` 里的 mapper 调用常量(动态计费主线)。
3. **同步更新 point_range**:让 min/max 与新费率一致。
4. 跑 `pytest tests/test_dynamic_estimate_trigger.py` 验证动态模型 `min < max`。
5. 跑 `pytest tests/test_<model>_billing.py` 验证新费率的积分曲线。

### 场景 B:只调某档位(如把 4K 改贵)

1. 改 billing mapper 里的查表常量(例:`OUTPUT_TOKENS_BY_SIZE["4K"] = 2400`)。
2. **不要动 `point_cost`**(那是兜底,跟档位无关)。
3. 同步更新 `point_range()` 用同样的 max 输入验证。
4. 跑 mapper 单测验证曲线。

### 场景 C:加新计费维度(如 num_input_images)

1. **后端**:`estimate_model_points` 加 `num_input_images: int = 0` 入参
   (`rh_models/api.py:475`)→ 塞占位 bytes 到 `request.images`,`estimate_cost`
   从 `len(request.images)` 读。
2. **调用方**:estimate query 加 `num_input_images`,从当前已连参考图数量读取。
3. **路由 handler**(`webapi.py:148`):FastAPI Query 加 `num_input_images: int = 0`。
4. **测试**:补 num_input_images 维度回归测试。

### 场景 D:加新计费维度(视频专用:resolution / duration / generate_audio / num_video_refs)

跟场景 C 同结构,但参数必须**双轨透传**:
- `resolution` / `duration` 既有顶层字段(Seedance 等从 `request.resolution` /
  `request.duration` 读),也有 params 字段(其他模型可能从 `request.params["resolution"]` 读)
- `generate_audio` 只放 `request.params["generate_audio"]`(Seedance 1.5 Pro mapper)
- `num_video_refs` 用占位 `[object()] * N` 让 `len()` 命中(`video_refs: list[MediaRef]`)

详见 `rh_models/api.py:475-600` 完整实现。

## 15.4 计费相关已知 bug 列表(防踩)

### Bug #1 — schema 暴露调用方无法感知的字段

**症状**:调用方按 `input_schema.quality` 渲染 quality 控件,切换 quality 时积分不变。

**根因**:`BananaProDef.node_def()` 声明了 `quality` 字段(透传给上游 API),
但官方计费曲线**不区分 quality 档位**(`OUTPUT_TOKENS_BY_SIZE` 按 image_size
固定 token,无 quality_factor)。调用方渲染 quality → 用户切 quality 看到积分不变
→ 困惑。

**修复**:**从 schema 移除该字段**。`estimate_cost` 也**故意不读**该字段。
详见 `models/image/defs.py:256-353` 的 `BananaProDef`。

**判定规则**:如果某 schema 字段对**计费曲线**无影响,**别暴露给调用方**。
否则要么计入计费,要么从 schema 移除。

### Bug #2 — estimate API 签名缺失参数 → FastAPI 422 拒绝

**症状**:调用方按 schema 渲染参数并传到 `/models/estimate`,引擎返回 422,
调用方拿到默认静态值。

**根因**:`estimate_model_points()` 签名只声明了部分参数(早期只有
`ratio / image_size / quality`),调用方发的 `duration / resolution /
generate_audio / num_video_refs` 被 FastAPI 422 拒绝。

**修复**:`estimate_model_points` 完整入参见 `rh_models/api.py:475`,路由 handler
见 `rh_models/webapi.py:148`。

**防御**:**新加 schema 字段时,必须同步加到 estimate API 签名 + 路由 Query**,
否则调用方调用必失败。改 schema 时也要 grep 一下 `estimate_cost` 里的读法。

### Bug #3 — point_range min == max → 调用方不调 estimate

**症状**:动态计费模型,但调用方显示固定积分,输入长文本积分不变。

**根因**:`point_range()` 用了太短的 max 输入文本,算出的积分仍是 1,与 min=1 相同。
调用方用 `min < max` 判断是否调 estimate,相等就不调。

**典型案例**:`IndexTTS2` 早期用 `estimate_index_tts2_points("你" * 300)`,
300 字符 × 3 bytes = 900 bytes,500 积分/M bytes → 实际 0.45 → 向上取整 1 积分。
`mimo_tts` 同病(600 积分/M bytes,300 字符 → 0.54 → 1 积分)。

**修复**:统一用 5000 字符当 max 输入(算出来 IndexTTS2=8,mimo_tts=9,差异可见)。

**回归测试**:`tests/test_dynamic_estimate_trigger.py:test_all_speech_models_have_dynamic_point_range`
固化"所有动态计费模型的 point_range.min 必须严格 < max"。

**防御**:**写完 `point_range()` 必须跑这个测试**。若 min==max,调用方预览 bug 立刻暴露。

### Bug #4 — _RATIO_SIZE_MAP 比例错误(2026-07 修复)

**症状**:gpt-image-2 切 image_size,4K 反而比 2K 便宜。

**根因**:早期 `_RATIO_SIZE_MAP` 把 `1024x1024 / 2048x2048 / 3840x2160` 这些
"占位值"塞给所有 ratio。例:2:3 + 2K 给的是 2048x2048 正方形,实际应为 2048x3072。
3:2 + 2K 给的是 2048x1152 (16:9 比例!),跟实际 3072x2048 完全不符。

**修复**:全表按 OpenAI 4 条硬约束(边 16 整除 / max edge ≤3840 / 比例精确 /
像素范围)重建。详见 `utils/mappers/gpt_image2_billing.py:48-69`。

**回归测试**:`tests/test_ratio_size_map_correctness.py:test_every_cell_is_a_valid_size`
遍历每个 cell 校验 4 条约束。

**防御**:新加 ratio 组合时,先用数学验证(两边都 16 整除 + 比例精确 + 像素范围),
再加表 + 跑测试。

### Bug #5 — 计费 mapper 读 request.images 但 estimate API 没传

**症状**:seedream5_pro / banana_pro 切换输入图数量,积分不变。

**根因**:`estimate_cost` 从 `len(request.images)` 读输入图数,但
`estimate_model_points` 创建 `GenerationRequest` 时**没传 images**,
`request.images` 默认空 → 永远 0。

**修复**:`estimate_model_points` 加 `num_input_images: int = 0` 入参,
创建 req 时塞占位 `images=[b""] * num_input_images`。调用方 estimate
同步加 `num_input_images` 字段,从已连参考图数量读。

**防御**:任何从 `request.images` / `request.video_refs` 读的 estimate_cost,
必须确认 estimate API 能传对应的 num_*_images/refs。

### Bug #6 — estimate API 参数 key 与 estimate_cost 读法不一致

**症状**:seedream5_pro 切 image_size,积分不变(永远按 2K 默认值)。

**根因**:`Seedream5ProDef.estimate_cost` 早期从 `request.params.get("size_mode")` 读,
但调用方通用约定 + `estimate_model_points` 写到 `request.params["image_size"]`,
key 不匹配 → 永远 None → 默认 2K。

**修复**:`defs.py:887` 改成 `request.params.get("image_size") or request.params.get("size_mode")`,
兼容两种 key(image_size 优先,size_mode 作 fallback)。

**防御**:**estimate_cost 读哪个 key,estimate API 就必须写哪个 key**。
如果模型有内部别名(早期 schema 的 size_mode),用 `or` 兼容。

## 15.5 改计费时自检清单

- [ ] 计费 mapper 单测更新(`tests/test_<model>_billing.py`)
- [ ] `point_range()` 与新费率一致(动态模型必须 `min < max`)
- [ ] `point_cost` 静态值同步(若改了)
- [ ] estimate API 签名包含所有 schema 参数(ratio/image_size/quality/resolution/duration/generate_audio/num_input_images/num_video_refs)
- [ ] schema 与 estimate_cost 读法一致(key 不漂移)
- [ ] 调用方 estimate query 同步加新参数(若新加)
- [ ] 调用方从已连参考数量 / 当前参数对象读 estimate 入参
- [ ] 跑全套测试:`pytest tests/`
- [ ] 端到端验证:`curl "http://127.0.0.1:8765/api/RH_ComfyUI/models/estimate?model=<name>&..."`

## 15.6 调试技巧

```bash
# 直接调 estimate API 看响应
curl "http://127.0.0.1:8765/api/RH_ComfyUI/models/estimate?model=seedream5_pro&image_size=1K&num_input_images=3"

# Python REPL 验证 billing mapper
python -c "
from RH_ComfyUI.utils.mappers.seedream5_pro_billing import calculate_seedream5_pro_points
print(calculate_seedream5_pro_points(0, '1K'))   # 30
print(calculate_seedream5_pro_points(0, '2K'))   # 60
print(calculate_seedream5_pro_points(3, '1K'))   # 34
"

# 看模型的 point_range
python -c "
from RH_ComfyUI.models import discover_builtin_models
from RH_ComfyUI.utils.backends import init_backends
from RH_ComfyUI.core.routing.registry import model_registry
init_backends(); discover_builtin_models()
for m in model_registry.by_modality(__import__('RH_ComfyUI.utils.core.request', fromlist=['TaskType']).TaskType.SPEECH):
    print(m.name, m.point_range())
"
```

## 15.7 历史改价记录(留痕)

| 日期 | 模型 | 变更 | 触发原因 |
|---|---|---|---|
| 2026-07 | seedream5_pro | estimate_cost 从 `size_mode` 改读 `image_size` | Bug #6 修复 |
| 2026-07 | seedream5_pro / banana_pro | estimate_model_points 加 `num_input_images` | Bug #5 修复 |
| 2026-07 | IndexTTS2 / mimo_tts | point_range max 改 5000 字符 | Bug #3 修复 |
| 2026-07 | gpt-image-2 _RATIO_SIZE_MAP | 24 个 cell 全表重建 | Bug #4 修复 |
| 2026-07 | estimate_model_points | 加 resolution/duration/generate_audio/num_video_refs | Bug #2 修复 |
| 2026-07 | banana_pro | schema 移除 quality 字段 | Bug #1 修复 |
| 2026-08 | seedance 2.0/2.5 | 有输入最低按 4s 计;金额先四舍五入到分;480p=856×480;2.5 开放 1080p(77/46) | 对齐火山方舟价表 |