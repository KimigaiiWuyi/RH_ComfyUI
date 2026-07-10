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
| `base_url` | OpenAI 兼容根地址;纯文生图拼 `/images/generations`,带参考图拼 `/images/edits` |
| `api_key` | Bearer 令牌(secret) |
| `weight` | 负载权重(≥1 整数,非法/缺失回落 1);仅 `Load_Balance_Mode=weighted` 时生效 |
| `models` | 嵌套重复组:`model_real_name`(内部模型,决定参数面与归属)→ `model_id`(发给供应商的 model 字段) |

千帆示例:`base_url=https://qianfan.baidubce.com/v2`,映射 `qwen_2512 → qwen-image`。

`model_real_name` 下拉项来自 `service_config.py` 顶部的静态常量
`_IMAGE_MODEL_REAL_NAMES`(避免配置模块加载期 import defs 触发循环);
**新增图片模型时同步该表**。

### 模态扩展约定(键名即协议)

池配置键按「协议_模态_Providers」命名:图片=`OpenAI_Image_Providers`;将来扩展语音/
视频池新增 `OpenAI_Speech_Providers` / `OpenAI_Video_Providers`,**行结构不变**
(enable/name/base_url/api_key/weight/models)。`providers.py` 的挂载逻辑已按池规格表
`_POOLS`(配置键 → 通道工厂)组织,新模态只需追加一条规格 + 对应通道类,解析/去重/
resync 全部复用。

## 13.3 同步时机与热更新语义(容易搞混,背下来)

- **凭证(api_key / base_url)**:`OpenAIImageChannel` 持有 `credentials_resolver`,
  每次 `check_available()` / `invoke()` 实时重读 SERVICE_CONFIG —— **改 key 即时生效,
  无需任何操作**(与 §11 热更新红线一致)。
- **供应商增删 / 模型映射 / 权重变动**:绑定关系是 `sync_openai_image_providers()` 注入
  channel_registry 的快照(权重在构造通道时固化),改完需要重挂 —— 两个时机:
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
  → `retryable=True` 切下一家;其中 **429/503 额外标 `transient=True`**,`run()` 会先在
  原通道退避重试一次再考虑切换(见 §2 生命周期)。
- `api.py::generate_image()` 按输入分流端点(**标准 OpenAI 协议**):
  - 纯文生图 → `POST {base_url}/images/generations`,JSON body `{model, prompt, n[, size]}`;
  - 带参考图 → `POST {base_url}/images/edits`,multipart 表单:`model/prompt/n[/size]`
    为普通字段,图片为文件部件(单图字段名 `image`,多图每张一个 `image[]`,
    与官方 SDK 惯例一致;字段表由纯函数 `_edits_fields()` 生成,可直接单测)。
  - 历史教训:曾把参考图 base64 塞进 `/images/generations` 的 `image` 字段 —— 那不是
    OpenAI 协议,千帆等供应商都不认,已废弃,勿回退。
  - 两端点响应结构一致:解析 `data[0].b64_json | url`,统一转 PNG 字节。
    空 key 不拼 Bearer(§11 红线)。
- `size` 由 `size_for(ratio, width, height)` 从枚举宽高比映射(无 ratio 时按宽高就近取)。
- 审计:`audit_key_prefix()` 记 key 前 6 位 → 统计表 `backend_key_prefix`;
  `backend_provider` = 供应商 `name`。

## 13.5 与 Gemini 通道的关系(banana2 多通道范例)

banana2 内置唯一通道是 `GeminiImageChannel`;经供应商池给 banana2 配一家
OpenAI 兼容供应商后,banana2 即成"gemini + 网关"双通道模型,由 LoadBalancer
自动分发与故障切换。这就是 §12 提到的"banana2 走网关 503 自动切 Gemini"的接线方式。

同模型跨供应商的**能力对齐由 ABC 基准类保证**:同一个内部模型(如 banana2)无论
路由到哪家供应商,输入 schema / 校验 / 归一化都来自同一个模型类;各通道只负责把
同一个 `GenerationRequest` 翻译成自家协议(Gemini SDK / OpenAI edits multipart / …)。
不做"通道级能力过滤"——同名模型即同种能力,是接入供应商时的前置约定。

## 13.6 运维:供应商对账

管理员命令 **`rh 供应商统计 [最近N天]`**(别名 `供应商对账`)按 `backend_provider`
聚合任务统计表:每家总单数 / 成功率 / 平均耗时 / 消耗积分,并附本次运行期的
熔断快照(🔴 熔断中 / 🟡 有连续失败计数 / 🟢 全健康)。数据源见 §10。

## 13.7 测试

`tests/test_openai_image.py`:配置解析(含 weight 回落)、sync/resync 幂等、
通道可用性与凭证热更新、错误翻译、`/images/edits` 端点分流与 multipart 字段形状。
`tests/test_provider_stats.py`:对账命令渲染。改本层代码必须保持全绿。
