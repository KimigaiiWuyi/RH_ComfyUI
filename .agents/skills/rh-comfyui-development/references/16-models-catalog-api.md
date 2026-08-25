# 十六、模型目录与 estimate API 契约

> 本章是 `/api/RH_ComfyUI/models` 系列接口的**完整契约**。调用方用这套接口
> 拉模型清单 + 实时算积分。改任何字段前**必读**:改 schema 字段会破坏调用方
> catalog 渲染,改 estimate 入参会破坏调用方实时预览。

## 16.1 端点总览

四个 GET 端点,**全部无需鉴权**(`/api/RH_ComfyUI/models/*` 路径,框架 `core_config.ENABLE_HTTP=True` 时挂载):

| 端点 | 用途 | 实现位置 |
|---|---|---|
| `GET /api/RH_ComfyUI/models` | 全量模型清单(按任务类型分组) | `rh_models/webapi.py:list_all_models` |
| `GET /api/RH_ComfyUI/models/summary` | 后端可用性摘要(总览面板) | `rh_models/webapi.py:backend_summary` |
| `GET /api/RH_ComfyUI/models/{task_type}` | 按 task_type 过滤(image/video/music/speech/asr) | `rh_models/webapi.py:list_models_by_task` |
| `GET /api/RH_ComfyUI/models/estimate` | 估算某模型在指定参数下的积分消耗 | `rh_models/webapi.py:estimate_model_cost` |

**注册顺序很重要**:`summary` 和 `estimate` 必须在 `{task_type}` 之前注册,
否则 Starlette 按注册顺序匹配,这两个字面路径会被 `{task_type}` 通配吃掉。
详见 `webapi.py:128` 处的注释。

## 16.2 `/models` 全量清单

**响应结构**:

```json
{
  "task_types": ["image", "video", "music", "speech", "asr"],
  "task_display": {
    "image": "图片生成",
    "video": "视频生成",
    "music": "音乐生成",
    "speech": "语音合成",
    "asr": "语音识别"
  },
  "total": 21,
  "available_count": 18,
  "models": [
    {
      "name": "seedream5_pro",
      "display_name": "Seedream 5.0 Pro",
      "task_type": "image",
      "backend": "seedream",
      "backend_model": "doubao-seedream-...",
      "backend_models": {"ark": "...", "runninghub": ""},
      "description": "...",
      "point_cost": 4,
      "point_range": {"min": 30, "max": 78},
      "priority": 70,
      "available": true,
      "unavailable_reason": null,
      "supported_tasks": ["image"],
      "input_schema": { /* 见 §16.4 */ },
      "output_schema": {...},
      "requirements": ["seedream_apikey"],
      "channels": [
        {
          "name": "ark",
          "vendor_model": "...",
          "available": true,
          "supports_cancel": true,
          "supports_remote_cancel": true
        }
      ],
      "execution_mode": "async_poll",
      "accepts_images": true,
      "max_input_images": 10,
      "supports_cancel": true,
      "supports_remote_cancel": true,
      "card": {...}
    }
  ]
}
```

### 取消能力字段(2026-08) — 引擎与调用方统一契约

调用方**必须以本清单决定能否取消**,禁止写死模型名单:

| 字段 | 位置 | 含义 / 调用方用法 |
|------|------|------|
| `supports_cancel` | 模型顶层 | 是否允许 `POST /tasks/cancel`。有 `channels` 时 = 各通道 OR |
| `supports_remote_cancel` | 模型顶层 | 是否至少一路可上游 DELETE。有 `channels` 时 = 通道 OR |
| `channels[].supports_cancel` | 通道 | **多通道时以当前选用通道为准**展示取消按钮 |
| `channels[].supports_remote_cancel` | 通道 | 当前通道是否 remote cancel;`rh_app` / Seedance RH 视频端为 false |

规则:
1. 单通道模型:读顶层 `supports_cancel` 即可
2. 多通道(如 Seedance ark + runninghub):读**当前通道**的 `channels[]` 字段;顶层 true 仅表示「有的通道可以」
3. `rh_app`:顶层与通道均为 **false**(不能 cancel,只能 resume 继续轮询)
4. 后端 `cancel_generation` / `POST .../tasks/cancel` 与上述标志一致:false 时返回 `ok=false`

实现:`rh_models/api.py` 的 `_channel_supports_cancel` / `_channel_supports_remote_cancel` / `_aggregate_cancel_flags`。  
**勿**因共用 `RH_apikey` 让 `rh_app` 与 `comfyui` 同值 —— 见 [§二十 §20.2](./20-cancel-resume-and-wire-audit.md)。

**契约红线**(HTTP 契约快照测试,见 `tests/test_http_contract.py`):
- 字段**只增不改**:加字段直接补到 `_MODEL_ENTRY_GOLDEN`,**禁止删/改名/变类型**
- 新字段必须有默认值(避免破坏旧调用方)

## 16.3 `/models/estimate` 实时积分预估

**Query 参数**(全部可选,缺失走模型默认):

| 参数 | 类型 | 适用模型 | 透传位置 |
|---|---|---|---|
| `model` | str | **必填** | — |
| `ratio` | str | 图片/视频(宽高比) | `request.ratio`(顶层)+ `params["ratio"]` |
| `image_size` | str | 图片(1K/2K/4K) | `params["image_size"]` |
| `quality` | str | 图片(low/medium/high) | `params["quality"]` |
| `resolution` | str | 视频(480p/720p/1080p) | `params["resolution"]` + `request.resolution`(顶层) |
| `duration` | int | 视频/音频(秒) | `params["duration"]` + `request.duration`(顶层) |
| `generate_audio` | bool | Seedance 1.5 Pro 等 | `params["generate_audio"]` |
| `num_input_images` | int | 按图计费的图片模型 | 占位 `images=[b""] * N` |
| `num_video_refs` | int | 按视频计费的视频模型 | 占位 `video_refs=[object()] * N` |

**响应结构**:

```json
{
  "model": "seedream5_pro",
  "point_cost": 34,
  "is_dynamic": true,
  "point_range": {"min": 30, "max": 78},
  "params": {
    "ratio": "1:1",
    "image_size": "1K",
    "quality": null,
    "resolution": null,
    "duration": null,
    "generate_audio": null,
    "num_input_images": 3,
    "num_video_refs": 0
  }
}
```

**字段语义**:
- `point_cost`:动态算出的积分;`is_dynamic=false` 时是静态 `point_cost`
- `is_dynamic`:True = `estimate_cost` 返回了与 `point_cost` 不同的值(说明有动态维度)
- `point_range`:从 `model.point_range()` 取,展示最低/最高积分
- `params`:本次参与计算的参数(echo 回去,便于调用方观测)

**已知问题**:异常路径(`model 不存在` / `estimate_cost 抛错`)仍返回 200,
`point_cost=0` + `error` 字段,调用方按"积分 0 + 显示错误"处理。**不要把异常当 4xx 抛**,
否则调用方积分预览会断流。

## 16.4 `input_schema` 结构(调用方渲染依据)

`input_schema` 是 `NodeDef.inputs` 序列化结果,告诉调用方"该模型有哪些参数、什么类型、什么枚举"。

**字段约定**(参见 `utils/core/types.py:PortSpec`):

```json
{
  "prompt": {
    "type": "text",            // text|integer|number|enum|boolean|list|image|...
    "required": true,
    "title": "提示词",
    "description": "...",
    "default": "..."           // 可选
  },
  "ratio": {
    "type": "enum",
    "default": "auto",
    "values": ["auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"],
    "title": "宽高比"
  },
  "image_size": {
    "type": "enum",
    "default": "2K",
    "values": ["1K", "2K", "4K"]
  },
  "duration": {
    "type": "integer",
    "default": 5,
    "minimum": 4,
    "maximum": 15
  },
  "images": {
    "type": "list",
    "min_items": 0,
    "max_items": 10,
    "item_type": "image"
  }
}
```

**调用方按 schema 渲染参数控件的约定**:

| schema.type | 调用方控件 | 数据来源 |
|---|---|---|
| `enum` + ratio | ratio 单选 | 当前宽高比 |
| `enum` + resolution | resolution 单选 | 当前分辨率 |
| `enum` + image_size | image_size 单选 | 当前尺寸档 |
| `enum` + quality | quality 单选 | 当前质量档 |
| `integer`/`number` + duration | 数值输入 | 当前时长 |

**契约**:schema 字段 → 调用方控件 → 当前参数 → `/models/estimate` Query

generate 走 `api.submit`:**同一把钥匙**写入 `request.params`(未知顶层 kwargs
会透传)。catalog-only 键不要升 `GenerationRequest` 字段,见 [§6.6](./06-entry-points.md)。

任何一环漏了都会断:
- schema 有字段但调用方没渲染 → 用户没法调该参数(无害)
- schema 没字段但调用方传了 → 422(无害)
- **schema 有字段但调用方 estimate 没传 → estimate 永远用默认值,预览不准**
- schema 有字段但 estimate API 签名没声明 → FastAPI 422 拒绝
- schema 有字段但 estimate_cost 没读 → 切换该字段积分不变

**防御**:加新参数面时,顺这条链全打通,跑 §15.5 自检清单。

## 16.5 `point_range` 的"双重作用"

**作用 1**:调用方判断是否调 estimate(`min < max` 才调)
**作用 2**:UI 展示"该模型最低/最高积分"(给用户预期)

`point_range()` 的实现要点:
- **动态模型必须 `min < max`**(`tests/test_dynamic_estimate_trigger.py` 强制)
- min 通常用空/最小输入(如 `""`、空 image 列表)
- max 通常用"常见上限"输入(5000 字符、5 张图、4K + 10 输入图 等)
- max 不要用**理论上限**(百万字符、100 张图) —— 会让用户误以为"输入多少都是这么多积分"
- max 也不要用**太短输入**(300 字符对按字节计费的语音模型算不出差异) —— 见 §15.4 bug #3

## 16.6 兼容性矩阵

| 改了什么 | 兼容性影响 | 处理 |
|---|---|---|
| 加 ModelEntry 字段(带默认) | 不破 | 同步加到 `_MODEL_ENTRY_GOLDEN` 契约快照 |
| 改字段类型 | **破** | 必须改调用方一起 |
| 删字段 | **破** | 永远不删,标 deprecated |
| 加 schema 字段(新参数) | 不破 | 同步调用方 estimate 入参 + 引擎 estimate API 签名 + estimate_cost 读取 |
| 改 schema 字段名 | **破** | 视为改 schema 类型 |
| 加 estimate API 入参 | 不破 | 缺失则用默认值 |
| 改 estimate API 入参名 | **破** | 必须同步调用方 estimate query |

## 16.7 调试与排查

```bash
# 1. 拿原始 schema
curl -s http://127.0.0.1:8765/api/RH_ComfyUI/models | python -m json.tool | less

# 2. 看某模型的 schema 字段
curl -s http://127.0.0.1:8765/api/RH_ComfyUI/models | \
  python -c "import sys,json; m=[x for x in json.load(sys.stdin)['models'] if x['name']=='seedream5_pro'][0]; \
  print(json.dumps(m['input_schema'], indent=2, ensure_ascii=False))"

# 3. 测 estimate
curl -s "http://127.0.0.1:8765/api/RH_ComfyUI/models/estimate?model=seedream5_pro&image_size=1K&num_input_images=3"

# 4. 看 point_range
curl -s http://127.0.0.1:8765/api/RH_ComfyUI/models | \
  python -c "import sys,json; m=[x for x in json.load(sys.stdin)['models'] if x['name']=='seedream5_pro'][0]; \
  print('point_range:', m['point_range'])"
```

**调用方拿到 422 / 默认值的排查步骤**:
1. 引擎 curl `/models/estimate?...` 看是否 422 或 200-but-default
2. 比对调用方发的 query 与 `webapi.py:estimate_model_cost` 签名
3. 比对调用方 estimate 入参与 schema(input_schema 是否声明了该字段)
4. 比对 estimate_cost 读 `request.params["..."]` 的 key 与 estimate API 写到 `params` 的 key