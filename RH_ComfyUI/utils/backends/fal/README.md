# utils/backends/fal

fal.ai 队列式生成通道。两类模型:

- **fal 专属**:`flux3max`、`h3max`、`h3maxturbo`(模型定义在 `models/video/fal.py`,fal 刊例价直接计价)。
- **共享模型**(模型×供应商正交,模型级计费曲线不动):`minimax_h3`(`minimax/h3/*`)、`seedance2`/`seedance2.5`(`bytedance/seedance-2.0|2.5/*`)、`banana1`/`banana2`/`banana2.1`/`banana2.lite`/`banana_pro`(nano-banana 系列,文生 + `/edit`)、`gpt-image-2`(`openai/gpt-image-2[/edit]`)。这些模型在各自 `channel_bindings()` 里追加 `fal_binding(name)`;fal 表达不了的参数(watermark / draft_task_id / return_last_frame / gif / xhigh·max / 帧位等)由 `FalChannel.invoke` 把 ValidationError 翻译成 `CAPABILITY + BEFORE_SEND`,换源且不计健康。

- `catalog.py` — 纯数据:模型名、各模型端点 id 与 app 前缀、档位常量、队列根地址。
- `config.py` — `SERVICE_CONFIG` 热读(FAL_* 键)。
- `billing.py` — 美元→积分的纯计算(仅 fal 专属模型用):按生成秒数 × 分辨率档(刊例价);reference 模式另含 reference token 超量费。
- `payload.py` — `prepare()` 按模型分流;`FalSubmit` 带 `app` 前缀与 `output`(video/image)。
- `payload_h3.py` — H3 Max / Turbo 组包:t2v / i2v / reference 三端点;`frame_mode`、关键帧位(h3max)、`reference_conditions`、token 估算。
- `payload_minimax_h3.py` — 普通 MiniMax H3 组包:`task_mode`/`frame_mode` → 三端点;参考模式不支持帧位。
- `payload_seedance.py` — seedance2 / 2.5 组包;2.5 的 `task_mode=edit/extend` → `task=editing/extension`,`draft` 直通。
- `payload_image.py` — banana 家族与 gpt-image-2 组包;传图即走 `/edit`;gpt-image 的 `ratio×image_size` 折成 `{width,height}`(16 倍数、≤3840、≤3:1)。
- `parse.py` — 建单 / 状态 / 结果响应的归一化(纯计算);`video_url_of` / `image_of`。
- `api.py` — `FalAPI`:POST 建单 → GET status 轮询 → GET result → 下载;`cancel()` 是 PUT;`resume()` 不重建单。
- `channel.py` — `FalChannel` + `bindings_for()` / `fal_binding()`。

`catalog` 保持零依赖:控制台加载早期会 import 它,不要让它读配置或发网络。
