# 十九、Seedance 2.5 与输入视频时长计费(会话交接)

> 2026-08 接入 Seedance 2.5,并修正 Seedance 全系列 token 计费中「输入视频时长」
> 被写死 5 秒的问题。本章给下次查阅用:模型契约、费率、透传链、测试入口。
>
> **2026-08 后续修订**:聚合网关(`aigc_system`)已注册 **seedance2.5** 通道
> (透传 V2);下文「仅 ark」已过时,见 §19.1 与 §19.9。

## 19.1 模型身份

| 项 | 值 |
|---|---|
| 模型 name | `seedance2.5` |
| display_name | Seedance 2.5 |
| 类 | `Seedance25Def` → `Seedance25VideoModel` |
| backend | `seedance` |
| **ark** Vendor Model | `doubao-seedance-2-5-260628`(方舟日期编码) |
| **gateway** Vendor Model | **`doubao-seedance-2.5`**(点分;见 `aigc_system` `SHARED_VENDOR_MODELS`) |
| 通道 | **ark + gateway**(网关经 `channel_registry` 注入 `gateway_slot{N}_seedance`) |
| 与 2.0 | **类型分开**(独立 name / backend_model,不合并进 seedance2) |

**⚠ 三套 id 勿混用**:

| 层 | 正确 | 错误 |
|----|------|------|
| RH host / catalog | `seedance2.5` | `seedance25` |
| ark body.model | `doubao-seedance-2-5-260628` | 点分 2.5 |
| 网关 body.model | `doubao-seedance-2.5` | host 名或 ark 日期 id |

文件:
- `RH_ComfyUI/models/video/defs.py` — `Seedance25Def`
- `RH_ComfyUI/models/video/overrides.py` — `Seedance25VideoModel`
- `RH_ComfyUI/utils/mappers/seedance_billing.py` — `estimate_seedance25_points`
- `aigc_system/seedance_gateway/models.py` — 网关 vendor 映射
- `aigc_system/docs/seedance-passthrough-v2-handover-2026-08.md` — 网关透传 + 信封错误面

## 19.2 能力差异(相对 seedance2)

| 维度 | Seedance 2.0 | Seedance 2.5 |
|---|---|---|
| 输出时长 | 4~15s | **4~30s**,任意模式可用 **-1** 自动(有参考视频时跟随输入) |
| 分辨率 | 480p~4k | **仅 480p / 720p** |
| 参考上限 | 图≤9 / 视频≤3 / 音频≤3(合计≤12) | 图≤30 / 视频≤10 / 音频≤10(**合计≤50**) |
| 输出格式 | mp4 | **mp4 / mov**(`output_format`) |
| 任务类型 | 自动文生/图生/首尾帧/多模态 | 另加 **`task_mode`**: auto / edit / extend |
| 宽高比 | 自定义 + adaptive | 编辑/延长/首帧·首尾帧 **必须 adaptive** |
| `camera_fixed` | schema 暴露(历史兼容) | **不支持**(官方仅 1.x;schema **无**该端口) |
| 费率 | 见 §15.2.4 | 无输入 **70** 元/M,有输入 **42** 元/M |

## 19.3 Token 公式(全 Seedance 系列)

官方:

```
tokens = (输入视频时长 + 输出视频时长) × 输出宽 × 输出高 × fps / 1024
points = ceil(tokens × 费率元/M × 100 / 1e6)   # 1 元 = 100 积分,最小 1
```

### 输入视频时长解析(`resolve_input_video_duration`)

优先级:

1. 显式 **`input_video_duration`**(秒)—— estimate API / `request.params`
2. 累加 `video_refs` 各段可解析时长(`duration` / dict / 数值)
3. 有参考但无时长 → **`5s × 段数`**(多段不再固定总 5s)
4. 无参考 → **0**

`duration=-1`(2.5 全模式自动时长):输出时长 = 输入总时长(>0),否则 15s。

### 2.5 费率与官方例

```
_SEEDANCE25_RATES = { "480p"/"720p": (70.00 无输入, 42.00 有输入) }
```

| 场景 | 积分 |
|---|---|
| 720p 5s 无输入 | **756**(7.56 元) |
| 720p 5s + 输入 5s | **908** |
| 720p 5s + 输入 15s | **1815** |

静态兜底 `point_cost=756`。

## 19.4 schema 要点(`Seedance25Def.node_def`)

- `task_mode`: auto | edit | extend
- `omni_reference_task_type`: auto | edit | extend(官方字段,与 duration 同级;**仅多模态参考**;编辑=edit、延长=extend、多参考=auto;文生/首帧/首尾帧**不写**)
- `frame_mode`: auto | first_last | reference
- `duration`: minimum **-1**, maximum **30**, default 5
- `ratio` default **adaptive**(图生/首尾帧/编辑强制 adaptive 的校验在 override)
- `output_format`: mp4 | mov
- **无** `camera_fixed`(官方「创建视频生成任务」API:`camera_fixed` 仅 Seedance 1.0 / 1.5;2.5 写入会 `InvalidParameter`)
- `backend_models={"ark": "doubao-seedance-2-5-260628"}`(不挂 runninghub)

校验红线(`Seedance25VideoModel.validate`):
- 图/视频/音频数量上限
- `duration` 为 4~30 或 **-1**(自动);**所有** task_mode / frame_mode 均可 -1,禁止再限成 edit/extend
- edit/extend 必须有参考视频
- **extend**：`classify_video_spec` 检查整段 prompt + OC 文本；没有「延长」则最前补 `延长该视频。`（前端会先写 `延长该视频 @视频 视频。`；已含「延长」不叠）
- edit/extend/首帧·首尾帧 的 ratio 必须 adaptive
- `camera_fixed=true` 直接拒绝(防存量前端/透传脏参数)
- Ark `render_create`: model 为 2.5 时**不写** body.`camera_fixed`
- aigc `GatewaySeedanceProvider`: 同上,复用 `_is_seedance25_model`(网关点分 `doubao-seedance-2.5`)

## 19.5 透传链(引擎 → 宿主 HTTP → 调用方 UI)

> 本节描述**典型宿主**如何透传 `input_video_duration` 等字段;引擎本身只认
> `GenerationRequest` / estimate 参数,不绑定具体前端产品。

### RH_ComfyUI

```
estimate_model_points(..., input_video_duration=?)
  → params["input_video_duration"]
  → Seedance*Def.estimate_cost
  → estimate_seedance*_points(..., input_video_duration=...)
  → resolve_input_video_duration(...)
```

- `rh_models/api.py` — `estimate_model_points` 入参
- `rh_models/webapi.py` — Query `input_video_duration`
- Ark provider:`duration=-1` 原样透传;`output_format=mov` 写入 body;
  `omni_reference_task_type` 仅 2.5 **多模态参考**写入(与 duration 同级);编辑=edit、延长=extend、多参考=auto;文生/首帧/首尾帧禁止(否则上游 TaskTypeConstraint);
  媒体上限 30/10/10,max_duration=30
- **Gateway**(`aigc_system.GatewaySeedanceProvider`):端点
  `/video/generation/passthrough/tasks`;body.model=`doubao-seedance-2.5`;
  `duration=-1` 同样原样透传;失败信封须抛 `user_message`(见 §19.9)

### canvas_backend

```
POST /api/canvas-backend/estimate
  body.input_video_duration → _rh_estimate_point_cost(...)

POST /api/canvas-backend/generate 预扣
  GenerateRequest.params.input_video_duration
  → _rh_estimate_point_cost_from_request
```

- `generate_api.py` — `_rh_estimate_point_cost` / `_from_request` / `api_estimate`
- `request_schemas.py` — `EstimateReq.input_video_duration`
- `_SEEDANCE2_FAMILY_RE = ^seedance2` **会匹配** `seedance2.5`(人像库 asset:// 可用)

### InfiniteCanvas 前端

| 位置 | 作用 |
|---|---|
| `src/utils/modelApi.ts` `fetchModelEstimate` | query 拼 `input_video_duration` |
| `GenerationNode.tsx` `estimateParams` | 累加已探测参考视频时长 |
| `GenerationNode.tsx` `buildGenerateRequest` | 写入 `params.input_video_duration` 供预扣 |
| `buildGenerateRequest.ts` | 无头提交路径同样写入 |
| `HomePage.tsx` | 主页草稿同逻辑 |
| `i18n.ts` | `'seedance2.5'` 中英文显示名 |
| `FrameModePicker` / `ConfigPicker` | 延长/编辑下拉;UI「自动」= `adaptive`/`duration=-1` |
| 交接 | InfiniteCanvas `docs/seedance25-modes-duration-popup-handover-2026-08.md` |

参考视频合法输入区间:

| 模型 | 单段参考视频 |
|------|----------------|
| Seedance **2.0** | **2~15s**(越界前端提示裁切) |
| Seedance **2.5** | **2~30s**(与输出上限对齐;前端按模型切换 max) |

输出最长 30s 是 2.5 的输出能力;勿再把 2.5 参考视频上限写死成 15s。

## 19.6 测试入口

```bash
# 引擎
cd plugins/RH_ComfyUI
pytest tests/test_seedance25_model.py tests/test_seedance_billing.py \
       tests/test_estimate_video_params.py -q

# 关键断言
# - 720p 5s 无输入 = 756
# - input_video_duration=15 → 1815
# - 3 段无时长 = 15s 输入
# - task_mode=edit 强制 adaptive + 参考视频
# - ark render duration=-1 / output_format=mov
```

## 19.7 改价 / 扩能力 checklist

- [ ] 改费率 → `_SEEDANCE25_RATES` + `tests/test_seedance_billing.py` 官方例
- [ ] 改时长上限 → defs PortSpec + Ark `max_duration` + override validate
- [ ] 改参考上限 → defs max_items + override MAX_* + Ark max_images/videos/audios
- [ ] 新 estimate 维度 → api + webapi + canvas EstimateReq + 前端 modelApi + estimateParams
- [ ] 前端显示名 → i18n `model.seedance2.5`
- [ ] 文档 §15(计费总览)若改公式同步改本节

## 19.8 相关外部文档

- InfiniteCanvas skills: `docs/skills/InfiniteCanvasDevelopment/references/15-estimate-flow-and-contract.md` §15.9
- InfiniteCanvas UI 交接: `docs/seedance25-modes-duration-popup-handover-2026-08.md`
- aigc 网关透传: `aigc_system/docs/seedance-passthrough-v2-handover-2026-08.md`
- 火山方舟教程 PDF:`docs/火山方舟_Doubao Seedance 2.5 教程_1786081569.pdf`
- ark Model ID:`doubao-seedance-2-5-260628`
- 网关 Model ID:`doubao-seedance-2.5`

## 19.9 网关通道与错误面(2026-08 增补)

| 项 | 说明 |
|----|------|
| 注册 | `aigc_system` `register_gateway` 把 seedance2.5 绑到 `gateway_slot{N}_seedance` |
| 端点 | `/video/generation/passthrough/tasks`(非旧 tasks,非 V3 硬切) |
| 与 ark 并存 | LoadBalancer 在 ark / gateway 间分摊(取决于配置启用与勾选) |
| 失败信封 | 网关可能 HTTP 200 + `{code,msg,data}`;provider 必须 `user_message=msg` |
| 用户症状 | 若只见「上游未返回任务ID」→ 查 aigc `parse_create` 信封路径,不是 RH 计费 |
| HappyHorse | **不**共享透传 path;勿因 Seedance 改 path 误伤 |

改 2.5 能力时 checklist 追加:

- [ ] 网关 `SHARED_VENDOR_MODELS` 与 catalog 勾选项
- [ ] 信封错误单测(`test_passthrough_parse_create_envelope_error_surfaces_msg`)
- [ ] 前端 task_mode / adaptive / duration=-1 与 RH validate 一致
