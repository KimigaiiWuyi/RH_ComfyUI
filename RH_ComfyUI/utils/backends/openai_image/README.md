# backends/openai_image — OpenAI 兼容生图供应商池

**不是 Adapter**(不进 backend_registry)。把网页控制台 `OpenAI_Image_Providers`
配置的每家 OpenAI 兼容供应商包装成一个 `OpenAIImageChannel`,经
`channel_registry.register_binding()` 挂到现有图片模型上,与内置通道一起
参与通用 LoadBalancer 的排序 / 熔断 / 故障切换。

| 文件 | 职责 |
|---|---|
| `api.py` | 通用 OpenAI 兼容生图客户端:`POST {base_url}/images/generations`,解析 `data[0].b64_json\|url`,统一转 PNG |
| `channel.py` | `OpenAIImageChannel`:凭证经 resolver 每请求实时解析(热更新);错误翻译成 `ChannelError` |
| `providers.py` | 配置解析 + `sync_openai_image_providers()`(启动 / `rh 刷新供应商` 时重挂绑定) |

维护须知:
- 凭证改动即时生效;**供应商增删 / 模型映射变动**需重跑 sync(命令 `rh 刷新供应商`);
- `model_real_name` 下拉项来自 `rh_config/service_config.py::_IMAGE_MODEL_REAL_NAMES`,
  新增图片模型时同步该表;
- 详细文档见 `docs/skills/rh-comfyui-development/references/13-openai-provider-pool.md`;
- 测试:`tests/test_openai_image.py`。
