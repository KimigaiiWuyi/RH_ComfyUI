# backends/kling — 可灵视频与图片

**不是 Adapter**(不进 backend_registry)。每个模型在 `channel_bindings()` 里挂
`KlingChannel`,通道名 `kling`,`weight=5`。同一账号共用在途计数。

| 文件 | 职责 |
|---|---|
| `catalog.py` | 视频 5 个、图片 4 个模型 id。纯数据 |
| `config.py` | 控制台键,每次调用现读 |
| `auth.py` | API Key,或 Access Key + Secret Key 的 JWT |
| `billing.py` | 官方人民币价 × 14 积分,向上取整 |
| `payload.py` | 按模型能力选路径并校验 |
| `parse.py` | `succeeded` / `succeed` 两种终态 |
| `api.py` | 创建、轮询、下载。POST 前写 wire |
| `channel.py` | 启用、勾选、并发闸 |

视频菜单对应 `kling-3.0-turbo` / `kling-3.0` / `kling-o1` / `kling-2.6` /
`kling-2.5-turbo`。`kling-3.0` 在无参考视频、主体和第三张图时走文生或图生;
否则走 `/omni-video/kling-3.0-omni`。

图片菜单对应 `kling-image-3.0` / `kling-image-o1` / `kling-image-common`(通用)
/ `kling-image-2.1`。通用的 `task_mode` 为 `expand`(扩图)或 `subject`(主体补全)。

`/image-to-video/kling-3.0` 的官方页面这次没有取到。字段按同系列图生接口:
`contents` 为提示词、首帧、可选尾帧;`settings` 含声音、多镜头、清晰度和时长,
不带纵横比。

鉴权:有 API Key 就用 `Authorization: Bearer <API Key>`。只有 AK/SK 时签
HS256 JWT。都空则不写这个头。

任务号存成 `v:` / `ig:` / `io:` / `im:` / `ex:` / `ms:` 加上游 id。
`resume_poll` 只按这个前缀继续查,不重新创建,也不挂远程取消。

控制台键:`Kling_Enable` / `Kling_Enabled_Models` / `Kling_API_Key` /
`Kling_Access_Key` / `Kling_Secret_Key` / `Kling_Endpoint` /
`Kling_Max_Concurrency`。默认关闭。默认域名 `https://api-beijing.klingai.com`。

`check_available` 零网络。参考视频只接受 URL,不代为发布。

测试:`tests/test_kling.py`、`tests/test_kling_billing.py`。
