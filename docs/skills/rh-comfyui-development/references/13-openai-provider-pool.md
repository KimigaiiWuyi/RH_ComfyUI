# 十三、OpenAI 兼容供应商池(网页配置,零代码给图片模型挂供应商)

> 2026-07 新增(commit `4571689` 对齐后端配置池)。给**现有图片模型**追加任意数量的
> OpenAI 兼容生图供应商(百度千帆 / OneAPI / NewAPI / OpenRouter 等),
> 全部在网页控制台配置,不写代码。

## 13.1 一句话原理

`SERVICE_CONFIG` 的 `OpenAI_Image_Providers`(`GsRepeatGroupConfig`,每行一家供应商)
→ `sync_openai_image_providers()`(`utils/backends/openai_image/providers.py`)按配置为每家
构造一个 `OpenAIImageChannel`,经 `channel_registry.register_binding(内部模型名, channel,
vendor_model=供应商侧模型名)` 挂到现有模型上 → 与内置通道一起参与通用 LoadBalancer
的排序 / 熔断 / 故障切换。**前端只看到一个模型,后端按供应商分发。**

## 13.2 配置结构(每行一家)

| 字段 | 含义 |
|---|---|
| `enable` | 是否启用该供应商 |
| `name` | 唯一标识 = 负载均衡成员名 = 统计表 `backend_provider` |
| `base_url` | OpenAI 兼容根地址,端点自动拼 `/images/generations` |
| `api_key` | Bearer 令牌(secret) |
| `models` | 嵌套重复组:`model_real_name`(内部模型,决定参数面与归属)→ `model_id`(发给供应商的 model 字段) |

千帆示例:`base_url=https://qianfan.baidubce.com/v2`,映射 `qwen_2512 → qwen-image`。

`model_real_name` 下拉项来自 `service_config.py` 顶部的静态常量
`_IMAGE_MODEL_REAL_NAMES`(避免配置模块加载期 import defs 触发循环);
**新增图片模型时同步该表**。

## 13.3 同步时机与热更新语义(容易搞混,背下来)

- **凭证(api_key / base_url)**:`OpenAIImageChannel` 持有 `credentials_resolver`,
  每次 `check_available()` / `invoke()` 实时重读 SERVICE_CONFIG —— **改 key 即时生效,
  无需任何操作**(与 §11 热更新红线一致)。
- **供应商增删 / 模型映射变动**:绑定关系是 `sync_openai_image_providers()` 注入
  channel_registry 的快照,改完需要重挂 —— 两个时机:
  1. 启动时 `RH_ComfyUI/__init__.py::init_pipeline_system`(在
     `discover_builtin_models()` **之后**调用,因为要挂到已注册模型上);
  2. 运行期管理员命令 **`rh 刷新供应商`**(`rh_admin/__init__.py`,别名
     `刷新供应商池` / `同步供应商`),回执启用家数与绑定条数。
- resync 逻辑:模块级 `_REGISTERED` 记录本层注入的 (model, channel) 对,
  先 `channel_registry.unregister()` 清历史再按当前配置重挂;同名供应商去重
  (后者跳过并 warning)。

## 13.4 执行链(channel.py / api.py)

- `OpenAIImageChannel.invoke()`:凭证不全 → `ChannelError(retryable=True)` 自动让路;
  未配置 vendor model → `retryable=False`(换通道也没用);上游 HTTP 错误/网络错误
  → `retryable=True` 切下一家。
- `api.py::generate_image()`:POST `{base_url}/images/generations`,
  body `{model, prompt, n[, size][, image(base64 数组)]}`,解析 `data[0].b64_json | url`,
  统一转 PNG 字节。空 key 不拼 Bearer(§11 红线)。
- `size` 由 `size_for(ratio, width, height)` 从枚举宽高比映射(无 ratio 时按宽高就近取)。
- 审计:`audit_key_prefix()` 记 key 前 6 位 → 统计表 `backend_key_prefix`;
  `backend_provider` = 供应商 `name`。

## 13.5 与 Gemini 通道的关系(banana2 多通道范例)

banana2 内置唯一通道是 `GeminiImageChannel`;经供应商池给 banana2 配一家
OpenAI 兼容供应商后,banana2 即成"gemini + 网关"双通道模型,由 LoadBalancer
自动分发与故障切换。这就是 §12 提到的"banana2 走网关 503 自动切 Gemini"的接线方式。

## 13.6 测试

`tests/test_openai_image.py`:配置解析(`resolve_provider_entries`)、
sync/resync 幂等、通道可用性与凭证热更新、错误翻译。
改本层代码必须保持全绿。
