# utils/backends/fal

fal.ai 队列式生成通道(FLUX 3 Max 视频)。

- `catalog.py` — 纯数据:模型名、三个端点 id、档位常量、队列根地址。
- `config.py` — `SERVICE_CONFIG` 热读(FAL_* 键)。
- `billing.py` — 美元→积分的纯计算:按生成秒数 × 分辨率档(刊例价)。
- `payload.py` — `prepare()` 校验 + 组包;`task_mode` / 图片数 / `frame_indices` 决定走哪个端点。
- `parse.py` — 建单 / 状态 / 结果响应的归一化(纯计算)。
- `api.py` — `FalAPI`:POST 建单 → GET status 轮询 → GET result → 下载;`cancel()` 是 PUT。
- `channel.py` — `FalChannel` + `bindings_for()`。

`catalog` 保持零依赖:控制台加载早期会 import 它,不要让它读配置或发网络。
