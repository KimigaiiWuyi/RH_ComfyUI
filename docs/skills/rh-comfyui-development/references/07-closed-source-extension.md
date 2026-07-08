# 七、闭源插件接入

原则:闭源包 **import 开源包**,反向零依赖。开源仓库里没有任何闭源代码、
条件 import 或另外的兼容插件生态逻辑;接入面只有 `RH_ComfyUI.core` 顶层公开接口。

## 7.1 两种注册途径

**途径 A:独立 gsuid_core 插件(推荐)**

```python
# 闭源插件 RH_ExtPlugin/__init__.py
from gsuid_core.server import on_core_start

@on_core_start
async def register_ext_models() -> None:
    from RH_ComfyUI.core import model_registry
    from .models import ExtSeedanceModel, InternalTTSModel

    model_registry.register(ExtSeedanceModel())
    model_registry.register(InternalTTSModel())
```

**途径 B:pip entry points(pip 分发的闭源包)**

```toml
# 闭源包 pyproject.toml
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
另外的兼容插件生态用 `name = "seedance2"` 即可把开源 seedance2 换成走内部网关的
版本,三大入口无感切换;起新名则与开源版并存。
注册顺序保证:开源 `discover_builtin_models()` 在 on_core_start 先跑,
闭源插件的 on_core_start 后跑(gsuid_core 按插件加载序执行钩子);
若不确定顺序,闭源侧可在自己的钩子里 import 开源 models 包强制先加载。

## 7.3 闭源模型类模板

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
    card = ModelCard(description="走另外的兼容插件生态内部网关的 Seedance 2.0")

    def input_schema(self) -> dict[str, PortSpec]: ...
    def channel_bindings(self) -> list[ChannelBinding]:
        return [ChannelBinding(InternalGatewayChannel(), vendor_model="sd2-internal")]
    async def execute_on_channel(self, request, binding, *, on_progress=None):
        return await binding.channel.invoke(request=request, on_progress=on_progress)
```

## 7.4 必须遵守的边界

1. **只从 `RH_ComfyUI.core` 顶层 import**,不深入 `core.*` 子模块
   (内核重组只保证顶层稳定);
2. **计费统计自动生效**:模型经 dispatch 执行即走 reserve/commit/refund 并
   落 RHComfyuiTaskRecord;统计只有 channel 名,私有 URL/参数不落库;
3. 另外的兼容插件生态独立钱包 → 实现自己的 `BillingPolicy` 子类,在**自己的入口**构造
   DispatchContext 时注入,不改开源 dispatcher;
4. 闭源专属命令 → gsuid_core 标准 SV + 构造 DispatchContext 调 dispatch;
5. 需要开源侧新钩子时,提交的是**通用**扩展点(新的 Policy/Channel 抽象),
   另外的兼容插件生态逻辑留在闭源包 —— 不接受按来源分叉的条件式提交。
