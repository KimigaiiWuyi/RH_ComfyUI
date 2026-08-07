# 十九、Seedance 2.5 与输入视频时长计费(会话交接)

> 2026-08 接入 Seedance 2.5,并修正 Seedance 全系列 token 计费中「输入视频时长」
> 被写死 5 秒的问题。本章给下次查阅用:模型契约、费率、透传链、测试入口。

## 19.1 模型身份

| 项 | 值 |
|---|---|
| 模型 name | `seedance2.5` |
| display_name | Seedance 2.5 |
| 类 | `Seedance25Def` → `Seedance25VideoModel` |
| backend | `seedance` |
| Vendor Model | `doubao-seedance-2-5-260628` |
| 通道 | **仅 ark**(复用 `Seedance_apikey_ark` / `Seedance_Enable_ark`) |
| 与 2.0 | **类型分开**(独立 name / backend_model,不合并进 seedance2) |

文件:
- `RH_ComfyUI/models/video/defs.py` — `Seedance25Def`
- `RH_ComfyUI/models/video/overrides.py` — `Seedance25VideoModel`
- `RH_ComfyUI/utils/mappers/seedance_billing.py` — `estimate_seedance25_points`

## 19.2 能力差异(相对 seedance2)

| 维度 | Seedance 2.0 | Seedance 2.5 |
|---|---|---|
| 输出时长 | 4~15s | **4~30s**,编辑/延长可用 **-1** 跟随输入 |
| 分辨率 | 480p~4k | **仅 480p / 720p** |
| 参考上限 | 图≤9 / 视频≤3 / 音频≤3(合计≤12) | 图≤30 / 视频≤10 / 音频≤10(**合计≤50**) |
| 输出格式 | mp4 | **mp4 / mov**(`output_format`) |
| 任务类型 | 自动文生/图生/首尾帧/多模态 | 另加 **`task_mode`**: auto / edit / extend |
| 宽高比 | 自定义 + adaptive | 编辑/延长/首帧·首尾帧 **必须 adaptive** |
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

`duration=-1`(仅 2.5 编辑/延长语义):输出时长 = 输入总时长(>0),否则 15s。

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
- `frame_mode`: auto | first_last | reference
- `duration`: minimum **-1**, maximum **30**, default 5
- `ratio` default **adaptive**(图生/首尾帧/编辑强制 adaptive 的校验在 override)
- `output_format`: mp4 | mov
- `backend_models={"ark": "doubao-seedance-2-5-260628"}`(不挂 runninghub)

校验红线(`Seedance25VideoModel.validate`):
- 图/视频/音频数量上限
- edit/extend 必须有参考视频
- edit/extend/首帧·首尾帧 的 ratio 必须 adaptive

## 19.5 透传链(引擎 → canvas → 前端)

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
  媒体上限 30/10/10,max_duration=30

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

参考视频 **合法输入区间仍为 2~15s**(上游参考硬限);输出最长 30s 是 2.5 的输出能力。

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
- 火山方舟教程 PDF:`docs/火山方舟_Doubao Seedance 2.5 教程_1786081569.pdf`
- Model ID 确认:`doubao-seedance-2-5-260628`
