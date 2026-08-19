# 七、外部插件接入

原则:外部包 **import 开源包**,反向零依赖。开源仓库里没有任何宿主业务代码、
条件 import 或按产品来源分叉的逻辑;接入面只有 `RH_ComfyUI.core` 顶层公开接口。

## 7.1 两种注册途径

**途径 A:独立 gsuid_core 插件(推荐)**

```python
# 外部插件 RH_ExtPlugin/__init__.py
from gsuid_core.server import on_core_start

@on_core_start
async def register_ext_models() -> None:
    from RH_ComfyUI.core import model_registry
    from .models import ExtSeedanceModel, InternalTTSModel

    model_registry.register(ExtSeedanceModel())
    model_registry.register(InternalTTSModel())
```

**途径 B:pip entry points(pip 分发的外部包)**

```toml
# 外部包 pyproject.toml
[project.entry-points."rh_comfyui.models"]
ext_plugin = "rh_ext_plugin.models:provide_models"
```

```python
def provide_models() -> list[type]:
    return [ExtSeedanceModel, InternalTTSModel]  # 类须可无参构造
```

启动时 `load_entry_point_models()` 自动装载;单个外部包损坏只 warning,
不拖垮开源插件。

## 7.2 覆盖开源同名模型

`ModelRegistry.register()` 对重名执行"后注册覆盖"(有 warning 日志)。
外部插件用 `name = "seedance2"` 即可把开源 seedance2 换成走自定义通道的
版本,三大入口无感切换;起新名则与开源版并存。
注册顺序保证:开源 `discover_builtin_models()` 在 on_core_start 先跑,
外部插件的 on_core_start 后跑(gsuid_core 按插件加载序执行钩子);
若不确定顺序,外部侧可在自己的钩子里 import 开源 models 包强制先加载。

## 7.3 外部模型类模板

```python
from RH_ComfyUI.core import (
    ModelCard, PortSpec, PortType, NodeOutput, ChannelError,
    ChannelBinding, ProviderChannel, GenerationRequest, VideoGenerationBase,
)

class InternalGatewayChannel(ProviderChannel):
    name = "internal-gateway"      # 统计表只落这个名字
    weight = 10

    async def check_available(self) -> bool:
        return bool(self._private_config("GATEWAY_TOKEN"))  # 只读配置

    async def invoke(self, **kwargs) -> NodeOutput:
        try:
            ...                    # 内部 URL / 签名逻辑,私有细节不出本包
        except TimeoutError as e:
            raise ChannelError(f"网关超时: {e}", retryable=True)

class ExtSeedanceModel(VideoGenerationBase):
    name = "seedance2"
    display_name = "Seedance 2.0(内部)"
    modality = TaskType.VIDEO
    point_cost = 10
    card = ModelCard(description="走外部插件内部通道的 Seedance 2.0")

    def input_schema(self) -> dict[str, PortSpec]: ...
    def channel_bindings(self) -> list[ChannelBinding]:
        return [ChannelBinding(InternalGatewayChannel(), vendor_model="sd2-internal")]
    async def execute_on_channel(self, request, binding, *, on_progress=None):
        return await binding.channel.invoke(request=request, on_progress=on_progress)
```

## 7.4 媒体外链化(对象存储等)扩展点

开源引擎**不得** import 其他本地包。需要把参考图
bytes 变成上游可 GET 的公网 URL 时,走 `core` 的 media publisher:

```python
# 宿主插件 @on_core_start
from RH_ComfyUI.core import set_media_publisher

async def my_publish(data: bytes, mime: str = "image/png") -> str:
    # 上传对象存储,返回 https URL;失败抛异常即可
    ...

set_media_publisher(my_publish)
```

- 未注册 → 内置 Adapter(如 Seedream)回落 data URL
- 已注册且失败 → `MediaPublishError`,通道层可翻成 retryable 以 failover
- 签名: `async (data: bytes, mime: str) -> str`

## 7.5 必须遵守的边界

1. **只从 `RH_ComfyUI.core` 顶层 import**,不深入 `core.*` 子模块
   (内核重组只保证顶层稳定);
2. **计费统计自动生效**:模型经 dispatch 执行即走 reserve/commit/refund 并
   落 RHComfyuiTaskRecord;统计只有 channel 名,私有 URL/参数不落库;
3. 外部插件独立钱包 → 实现自己的 `BillingPolicy` 子类,在**自己的入口**构造
   DispatchContext 时注入,不改开源 dispatcher;
4. 外部专属命令 → gsuid_core 标准 SV + 构造 DispatchContext 调 dispatch;
5. 需要开源侧新钩子时,提交的是**通用**扩展点(新的 Policy/Channel 抽象),
   宿主业务逻辑留在外部包 —— 不接受按来源分叉的条件式提交;
6. **反向零依赖**:开源仓库不得 import 宿主业务包(含 soft-import 任意
   宿主 HTTP 入口 / 外部网关 / 账号系统等);宿主能力一律通过扩展点注入。
7. **文档与注释中立**:示例用「宿主 / 调用方 / 外部插件」,不把具体产品
   路径写成引擎架构前提;公开 API 对任意 bot_id / HTTP 宿主通用。
   **不得**在代码、注释或本 SKILL 中暴露具体前端产品或宿主后端的存在。
8. **取消 / resume / wire**:外部通道同样须在 create 后 `bind_vendor_cancel`、
   POST 前 `set_wire_*`;目录 `supports_remote_cancel` 须诚实(无 cancel API
   则 false)。见 [§二十](./20-cancel-resume-and-wire-audit.md)。

## 7.6 通道绑定热重挂

凭证热读(`check_available`)解决不了「给哪个模型挂哪条通道」这份快照。
外部插件按配置 `register_binding` 的,必须提供可幂等重挂函数,并登记:

```python
from RH_ComfyUI.core import register_resync_hook, bind_config_resync

def my_register() -> None:
    # 先 unregister 本层历史绑定,再按当前配置 register_binding
    ...

register_resync_hook("my_gateway", my_register)
bind_config_resync(MY_CONFIG, frozenset({"Slot_Enable", "Slot_Models"}))
```

触发时机:`rh 刷新供应商`;以及被监视配置键的 `set_config` 成功写入。
钩子失败彼此隔离,开源侧不 import 外部插件。
