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

视频菜单对应 `kling-4.0` / `kling-3.0-turbo` / `kling-3.0` / `kling-o1` /
`kling-2.6` / `kling-2.5-turbo`。`kling-3.0` 在无参考视频、主体和第三张图时
走文生或图生;否则走 `/omni-video/kling-3.0-omni`。

`kling-4.0` 三路分发:纯提示词走 `/text-to-video/kling-4.0`;只带关键帧图片走
`/image-to-video/kling-4.0`(首帧/中间帧/尾帧,合计 ≤10 且 ≤ 时长+1,不传纵横比);
带 refer 图、参考视频、主体、音色,或文生选了 `adaptive` 宽高比,走
`/omni-video/kling-4.0`(素材合计 ≤15:图 ≤10 / 视频 ≤5 且 base 只能 1 段 /
主体 ≤7 / 音色 ≤3)。图片角色默认按位置(首图首帧、末图尾帧、其余中间帧),
`params["frame_roles"]` 逐张指定 `first/middle/last/refer`。素材 id 编成
`image_N` / `video_N` / `element_N` / `voice_N`,提示词里 `@` 引用。
4.0 没有 audio / multi_shot 字段,声音写进提示词;提示词按英文 ≤26000 /
非英文 ≤11000 加权计长。

4.0 计费:无输入视频 720p ¥1.0/s、1080p ¥1.5/s;有输入视频 720p ¥0.6/s、
1080p ¥0.9/s,计费秒数 = 输入 > 输出 ? 输入+输出 : 输出×2。输入视频总秒数
读 `input_video_duration` 参数或参考视频自带时长,都没有按 5 秒/段估。

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
