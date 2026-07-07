# 九、后端(utils/backends)、映射器(utils/mappers)与配置体系

## 9.1 后端 Adapter 层

`utils/backends/` 是所有上游通信细节的家。每个后端是一个 `Adapter` 子类
(`base.py`),注册进 `backend_registry`(`init_backends()` 启动时调用)。

| 后端名(backend 字段) | 目录 | 上游 | 说明 |
|---|---|---|---|
| `comfyui` | `comfyui/` | 本地/远程 ComfyUI | WebSocket + workflow JSON;`set_workflow_override()` 支持 mapper 按输入切换工作流(如 Wan t2v/i2v) |
| `gpt-image-2` | `gpt_image2/` | OpenAI 兼容协议 | 文生图/图生图/编辑自适应;凭证可指向 OneAPI/NewAPI 等网关(注意名字带**连字符**) |
| `rh_app` | `rh_app/` | RunningHub AI 应用 | webapp id 即 `workflow_file` |
| `minimax` | `minimax/` | MiniMax | 图片 + 语音(T2A) |
| `mimo` | `mimo/` | MiMo TTS | 语音 |
| `seedance` | `seedance/` | 字节 Seedance 系列 | 内部有独立的 Provider 层(ARK/Gateway/RunningHub 三供应商)+ 负载均衡 + 熔断 + Dry-Run |

Adapter 协议(5 成员):`name` / `check_available()` / `get_unavailable_reason()` /
`capabilities()` / `execute(request, node, on_progress) -> NodeOutput`。

**分工边界**:Adapter 管通信与厂商协议;模型类(models/)管参数面与校验;
`AdapterChannel`(models/bridge.py)把 Adapter 适配为 ProviderChannel 供
ABC 执行链使用。新上游 = 新 Adapter(或直接写 ProviderChannel,见 04 章)。

### Seedance 的 Provider 子层

`backends/seedance/` 内部:`provider.py`(SeedanceProvider 细粒度抽象:
render/parse/poll + 形态/分辨率支持声明)+ `registry.py`(供应商选择)。
模型 YAML 时代的 `backend_models`(ark/gateway/runninghub → 各家模型 ID)
现在声明在 defs.py 的 `node_def()` 里;节点级 `provider` 字段可固定走某家。

## 9.2 映射器(utils/mappers)

mapper = "GenerationRequest → 厂商 payload / ComfyUI workflow" 的纯函数。
`node_def()` 里 `mode="programmatic"` + `mapper_func=xxx` 直接引用
(defs.py 顶部 `from ...utils.mappers.xxx import yyy`);
`mode="declarative"` + `mappings=[{source, target}]` 用于简单的
ComfyUI 字段注入。

| 文件 | 服务的模型 |
|---|---|
| `gpt_image2.py` | banana2 / banana_pro / gpt-image-2 |
| `image_edit.py` | qwen_2511(图片编辑) |
| `minimax_text2image.py` | minimax_image01 |
| `video.py` | wan2.2_videogen(含 t2v/i2v 工作流切换) |
| `music.py` | ace_step1.5 |
| `speech.py` | IndexTTS2(含参考音频注入) |
| `mimo_speech.py` / `minimax_speech.py` | mimo_tts / minimax_t2a_speech |

改某模型的请求组装 → 改对应 mapper;改参数面(校验/前端表单)→ 改 defs 的
PortSpec。两者必须同步(mapper 消费的字段要在 inputs 里声明)。

## 9.3 配置体系(rh_config/)

两个配置实例,都自动注册进 gsuid_core 网页控制台:

**SERVICE_CONFIG(service_config.py)— 上游凭证/地址**

| 键 | 用途 |
|---|---|
| `ComfyUI_BaseURL` | ComfyUI 地址 |
| `RH_apikey` | RunningHub |
| `OpenAI_Image_apikey` / `OpenAI_Image_BaseURL` | OpenAI 兼容生图 |
| `MiniMax_apikey` / `MIMO_apikey` | MiniMax / MiMo |
| `Seedance_apikey_{ark,gateway,runninghub}` + `Seedance_BaseURL_*` + `Seedance_Enable_*` | Seedance 三供应商 |
| `Seedance_Load_Balance` / `Seedance_Failure_Threshold` / `Seedance_Dry_Run` | 负载均衡策略/熔断阈值/干跑 |

**PLUGIN_CONFIG(plugin_config.py)— 插件行为**

| 键 | 用途 |
|---|---|
| `Max_Concurrency` | 全局并发闸大小 |
| `Default_Point` | 新用户初始积分 |
| `Draw_Point` / `Edit_Image_Point` / `Music_Point` / `Speech_Point` / `Video_Point` | 各任务兜底价格(模型自带 point_cost 优先) |

规则:
- 模型可用性由 `required_config` / `requirements` 引用这些键,缺失自动
  标不可用并给出人话原因;
- 新上游的配置键加在 SERVICE_CONFIG 并配 `GsDivider` 分组;
- 读配置一律 `SERVICE_CONFIG.get_config(key).data`,不要缓存到模块级
  (网页控制台改完要即时生效)。

## 9.4 凭证热更新(redline:中途改 key 不要重启)

用户经常在 Web 控制台改完 `RH_apikey` / `Seedance_apikey_ark` /
`ComfyUI_BaseURL` 之类就直接复用,要求**不重启进程**就生效。下面是
每个后端的现状与写法约束,违反会出现 `LocalProtocolError: Illegal
header value b'Bearer '` 或旧 URL 持续报错。

| 后端 | 凭证来源 | 实现 | 是否热更新 |
|---|---|---|---|
| `mimo` | `MIMO_apikey` | `@property api_key` 直接读 `SERVICE_CONFIG` | ✅ 每次请求即时 |
| `minimax` | `MiniMax_apikey` | `@property api_key` 直接读 `SERVICE_CONFIG` | ✅ 每次请求即时 |
| `gpt-image-2` | `OpenAI_Image_apikey` / `BaseURL` | `api_key` 用懒加载 + `refresh_config()` | ✅ executor 入口每次 `refresh` |
| `rh_app` | `RH_apikey` | `@property api_key` + `_require_api_key()` | ✅ 每次请求即时 |
| `comfyui` | `ComfyUI_BaseURL` + `RH_apikey` | `url` / `server_address` / `api_key` 全 `@property` | ✅ 每次请求即时 |
| `seedance` | `Seedance_apikey_*` + `Seedance_BaseURL_*` | provider 用 `update_credentials()` | ✅ executor `_get_or_create_provider` 每次比对新旧值再热更新 |

### 红线:不要在 `__init__` 里把 `api_key` 存成实例属性

❌ 反例(老 rh_app/api.py,2026-07 修):

```python
def __init__(self) -> None:
    self.api_key = SERVICE_CONFIG.get_config("RH_apikey").data  # 启动时是空就永远是空
```

✅ 正例一: `@property` 直接读配置(`mimo` / `minimax` / `rh_app` 用法)

```python
@property
def api_key(self) -> str:
    """动态读取,避免单例把空 key 缓存到进程退出"""
    return SERVICE_CONFIG.get_config("RH_apikey").data or ""
```

✅ 正例二: 懒加载 + `refresh_config()`(`gpt-image-2` 用法)

```python
def __init__(self) -> None:
    self._api_key: Optional[str] = None  # sentinel,首次访问才读

@property
def api_key(self) -> str:
    if self._api_key is None:
        self._api_key = SERVICE_CONFIG.get_config("OpenAI_Image_apikey").data or ""
    return self._api_key

def refresh_config(self) -> None:
    self._api_key = None  # 下次访问重读
```

executor 在 `check_available()` / `execute()` 入口先调 `refresh_config()`。

✅ 正例三: 显式 `update_credentials()`(`seedance` 用法)

provider 内部存 `self.api_key`,但保留 `update_credentials()`;
executor 的 provider 缓存按"凭证是否变了"决定要不要调:

```python
cached = self._provider_cache.get(name)
if cached is not None:
    old_key, old_url = cached.api_key, cached.base_url
    if old_key != creds.api_key or old_url != creds.base_url:
        cached.update_credentials(api_key=creds.api_key, base_url=creds.base_url)
    return cached
```

### URL 也算"凭证派生字段"

`comfyui/api.py` 的 `self.url` / `self.server_address` 历史上是
`__init__` 里算好的,RunningHub 模式下还是 `f".../proxy/{api_key}"`
拼出来的。修了 base_url / api_key 之后 URL 必须**也跟着重算** —— 否则
会带着旧 host / 旧 proxy 路径继续打。ComfyUIAPI 现在把这四个字段
(`is_runninghub` / `api_key` / `url` / `server_address`)全改成
`@property`,每次访问都按当前 config 重算。

### 防御:`Bearer ` 不能进 httpx 头

老 rh_app 的另一个坑:`_headers()` 拼出 `f"Bearer {self.api_key}"`,
key 是空串就变成 `"Bearer "`(注意后面那个空格),httpx 看到非法头部会
抛 `LocalProtocolError: Illegal header value b'Bearer '`,traceback
直接糊到用户脸上。修法:

```python
def _headers(self) -> Dict[str, str]:
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if self.api_key:  # 空 key 不要拼 Bearer,让上游按 401/403 报清晰错误
        headers["Authorization"] = f"Bearer {self.api_key}"
    return headers

def _require_api_key(self) -> str:
    """业务入口前置守卫,key 为空时抛友好中文错误而不是 LocalProtocolError"""
    key = self.api_key
    if not key:
        raise RuntimeError("[RHApp] 未配置 RunningHub API Key,请在 Web 控制台配置 RH_apikey 后重试")
    return key
```
