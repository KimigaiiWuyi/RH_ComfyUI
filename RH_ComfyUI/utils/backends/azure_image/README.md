# backends/azure_image — Azure gpt-image 通道

**不是 Adapter**(不进 backend_registry)。`AzureImageChannel` 挂到
`gpt-image-2` / `gpt-image-2.5-sunburst` / `gpt-image-2.5-flare`,
与已有通道一起参与负载均衡。通道名 `azure`,`weight=3`。

| 文件 | 职责 |
|---|---|
| `catalog.py` | 三个模型 id。纯数据,控制台加载早期会读 |
| `api.py` | Foundry v1 客户端。凭证 `@property` 热读 |
| `channel.py` | 勾选 / 并发闸。同一资源共用在途计数 |
| `setup.py` | `register_azure_image()` 差量重挂 |

请求约定(不要改回 OpenAI 兼容生图那套):

- 鉴权头是 `api-key`。凭证为空时不写这个头。
- URL:`{endpoint}/openai/v1/images/generations|edits?api-version=preview`。
  部署名在 body/form 的 `model`,不进 path。
- 文生 JSON **不带** `response_format`。
- 有参考图走 `/images/edits` multipart:单图字段 `image`,多图 `image[]`。
- `xhigh` / `max` 原样透传。
- 旧配置 `2025-04-01-preview` 改写成 `preview`。
- HTTP 错误保留 `error.code`。404 不在客户端里重试成 500。日志前缀 `[AzureImage]`。
- POST 前 `set_wire_from_http_body`,统计以最终载荷为准。

控制台键:`Azure_Image_Enable` / `Azure_Image_Deployment` /
`Azure_Image_apikey` / `Azure_Image_Endpoint` / `Azure_Image_ApiVersion` /
`Azure_Image_Max_Concurrency`。Enable 与 Deployment 在重挂监视里;凭证热读。

`check_available` 零网络。默认关闭总开关。默认勾选两款 2.5。

测试:`tests/test_azure_image.py`。
