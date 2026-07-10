# 三、新增/修改模型

模型定义全部在 `RH_ComfyUI/models/<模态>/defs.py`,每个模型一个类。
按复杂度选三条路径之一。

## 3.1 路径 A:参数面简单 + 复用现有 backend(最常见)

适用:参数用 PortSpec 就能表达(必填/枚举/数量/数值范围,无跨字段约束),
执行走现有 backend(comfyui / rh_app / gateway / gpt-image-2 / seedance 等)。

在对应模态 defs.py 加一个类(参考同文件现有类):

```python
class NewImageDef(ImagePipelineModel):
    """新模型 — 一句话说明"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="new_image",                    # 三入口与统计的唯一主键,慎改
            display_name="新模型",
            task_type=TaskType("image"),
            backend="comfyui",                   # 对应 utils/backends 注册名
            point_cost=2,                        # 积分价格
            description="一句话简介(HTTP 清单展示)",
            knowledge_content=(                  # AI 知识库:Agent 选型的依据
                "优势:...\n"
                "适用场景:...\n"
                "不适用场景:...\n"
            ),
            requirements=["comfyui_url"],        # 仅 HTTP 清单展示用(informational);
                                                 # 实际可用性由通道 check_available() 判定
            workflow_file="new_image.json",      # ComfyUI 工作流(如适用)
            mode="declarative",                  # declarative | programmatic
            mappings=[                           # declarative:字段 → workflow 节点
                {"source": "prompt", "target": "108.inputs.text"},
            ],
            # mode="programmatic" 时改用:
            # mapper_func=my_mapper,             # from ...utils.mappers.xxx import
            inputs={
                # title=前端面板短标题(几个字);description=完整说明(Agent 消费,缺 title 时前端回退用它)
                "prompt": PortSpec(type=PortType.TEXT, required=True, title="提示词", description="生成描述"),
                "width": PortSpec(type=PortType.INTEGER, default=720, minimum=256, maximum=2048, title="宽度"),
            },
            outputs={"image": PortSpec(type=PortType.OUTPUT_IMAGE, description="生成的图片")},
            capabilities=CapabilityManifest(
                supported_tasks=["image"],
                mode="sync",                     # sync | async_poll
                priority=60,                     # 路由优先级,越大越优先
            ),
        )
```

最后**追加到文件末尾的 `ALL_MODELS` 列表**,重启即生效
(命令 / Agent / HTTP 三入口自动可见,无需其它注册步骤)。

## 3.2 路径 B:有跨字段约束(叠加校验类)

例:"多参考素材合计 ≤12"、"像素积 ≤720P"、"不支持有声"。

1. 在 `models/<模态>/overrides.py` 写能力/校验类,继承模态桥接类:

```python
class NewVideoModel(VideoPipelineModel):
    def __init__(self, node: NodeDef) -> None:
        super().__init__(node)
        self.supported_shapes = {VideoTaskShape.TEXT2VIDEO, VideoTaskShape.IMAGE2VIDEO}
        self.supported_resolutions = ["480p", "720p"]
        self.card = ModelCard(description="...", strengths=[...], categories=[...])

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)      # ★ 先跑 schema 通用校验
        if request.generate_audio:
            raise ValidationError(f"{self.display_name} 不支持生成有声视频")
```

2. defs.py 里的定义类改为继承它(参考 `Seedance2Def(SeedanceVideoModel)`);
3. 在 `tests/test_schema_validation.py` 补跨字段约束用例(参考 Seedance/Wan)。

校验规则:`validate()` 无副作用、无网络;错误信息面向最终用户,写人话
(命令与画布都会直接展示)。

## 3.3 路径 C:全新执行链(不走现有 Adapter)

1. 在 `utils/backends/` 写 HTTP 客户端(通信细节只在这里);
2. 写 `ProviderChannel` 子类(见 [04 章](./04-channels-and-providers.md));
3. 直接继承模态 ABC(`ImageGenerationBase` 等),实现
   `input_schema()` / `channel_bindings()` / `execute_on_channel()`,
   声明 `required_config`;
4. 用 `@register_model` 装饰(类须可无参构造),并在 `models/__init__.py`
   import 该模块使装饰器生效(此路径无需 NodeDef;命令/Agent/HTTP 清单
   均自动可见 —— HTTP 清单对无 NodeDef 模型的补录 2026-07-10 起生效);
5. 配置键在 `rh_config/comfyui_config.py` 注册(网页控制台可配)。

## 3.4 常改字段速查

| 要改什么 | 改哪里 |
|---|---|
| 积分价格 | defs 类 `node_def()` 的 `point_cost` |
| 路由优先级 | `capabilities.priority` |
| Agent 选型描述 | `knowledge_content`(知识库)+ overrides 的 `card`(HTTP card 字段) |
| 参数默认值/枚举 | `inputs` 的 PortSpec |
| 请求组装逻辑 | `utils/mappers/` 对应 mapper 函数,或 declarative `mappings` |
| 下线模型 | 从 `ALL_MODELS` 移除(勿删类,便于回滚) |

## 3.5 修改后的验证

```bash
python -m pytest tests/ -q
ruff check RH_ComfyUI/models
```

再做一次启动冒烟(不发真实请求):

```python
from RH_ComfyUI.utils.backends import init_backends; init_backends()
from RH_ComfyUI.models import discover_builtin_models
discover_builtin_models()   # 看日志确认新模型已注册
```
