# 十七、新增/修改模型 与 计费改动 完整交接清单

> 给"接手这个项目的人"的 SOP。**不**讲思路(思路看 §03),**只**讲按什么顺序改、改什么文件、跑什么测试、容易漏什么。

## 17.1 新增一个图片模型(基于现有 backend)

**适用场景**:新加一个走 `gpt-image-2` / `seedream` / `rh_app` / `nano_banana` 等已有 backend 的图片模型。

### Step 1 — 写 defs.py

文件:`RH_ComfyUI/models/image/defs.py`(或 video/music/speech/asr)

```python
@register_model  # 可选:也可加到 ALL_MODELS 末尾
class NewImageModel(ImagePipelineModel):
    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="new_image_model",                  # 契约 name,前端 catalog 用
            display_name="新图片模型",
            task_type=TaskType("image"),
            backend="gpt-image-2",                   # 选现有 backend
            backend_model="...",
            point_cost=4,                            # 静态兜底
            description="...",
            knowledge_content="...",
            requirements=["gpt_image2_apikey"],
            mode="programmatic",                     # 或 "declarative"
            mapper_func=_gpt_image2_mapper,          # 复用现有 mapper
            inputs={
                "prompt": PortSpec(type=PortType.TEXT, required=True, ...),
                "ratio": PortSpec(type=PortType.ENUM, default="auto", values=[...]),
                "image_size": PortSpec(type=PortType.ENUM, default="2K", values=["1K","2K","4K"]),
                "quality": PortSpec(type=PortType.ENUM, default="medium", values=["low","medium","high"]),
                # 若不区分计费维度,**不要暴露** schema 字段(详见 §15.4 bug #1)
            },
            ...
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """若复用现有计费:调对应 billing mapper。"""
        from ...utils.mappers.<model>_billing import estimate_<model>_points
        image_size = request.params.get("image_size")
        num_input_images = len(request.images) if request.images else 0
        return estimate_<model>_points(num_input_images, image_size)

    def point_range(self) -> tuple[int, int]:
        """动态模型:必须 min < max(详见 §15.4 bug #3)"""
        from ...utils.mappers.<model>_billing import estimate_<model>_points
        return (
            estimate_<model>_points(0, "1K"),       # min: 最小输入
            estimate_<model>_points(10, "4K"),      # max: 常见上限
        )
```

### Step 2 — 加到 ALL_MODELS

文件末尾:

```python
ALL_MODELS = [
    AnimaDef,
    ...
    NewImageModel,   # ← 加这里
]
```

### Step 3 — 写 billing mapper(若新计费曲线)

文件:`RH_ComfyUI/utils/mappers/<model>_billing.py`

```python
"""新模型动态积分计价(取自官方文档)
计价规则:
  - ...
  - ...

point_cost 仅作兜底。
"""
from typing import Optional

# 常量集中顶部
INPUT_COST_PER_IMAGE_POINTS: int = 2
OUTPUT_TOKENS_BY_SIZE: dict[str, int] = {"1K": 1120, "2K": 1120, "4K": 2000}
OUTPUT_POINTS_PER_MILLION_TOKENS: int = 12_000

def calculate_<model>_points(num_input_images: int, image_size: Optional[str]) -> int:
    """纯函数:计算总积分 = 输入积分 + 输出积分,最小 1 积分。"""
    # ...

def estimate_<model>_points(num_input_images: int = 0, image_size: Optional[str] = None) -> int:
    """薄壳:供 estimate_cost 调用。"""
    return calculate_<model>_points(num_input_images, image_size)

__all__ = ["calculate_<model>_points", "estimate_<model>_points", ...]
```

### Step 4 — 跑契约自检

```bash
# 1. 注册表能正确发现新模型
python -c "
from RH_ComfyUI.models import discover_builtin_models
from RH_ComfyUI.utils.backends import init_backends
from RH_ComfyUI.core.routing.registry import model_registry
init_backends(); discover_builtin_models()
m = model_registry.get('new_image_model')
print('point_cost:', m.point_cost)
print('point_range:', m.point_range())
print('available:', __import__('asyncio').run(m.check_available()))
"

# 2. 端到端 estimate API
curl -s "http://127.0.0.1:8765/api/RH_ComfyUI/models/estimate?model=new_image_model&image_size=2K&num_input_images=3"

# 3. 跑回归测试
pytest tests/test_dynamic_estimate_trigger.py -v       # 动态模型 point_range.min < max
pytest tests/test_<model>_billing.py -v                  # 计费曲线单测
pytest tests/test_model_schema.py -v                     # schema 契约快照
```

### Step 5 — 前端同步(谁负责谁改)

- **新 schema 字段**:前端 `GenerationNode.estimateParams` 要读取(`modelApi.ts:140` + `GenerationNode.tsx:1000`)
- **新计费维度**:前端 `fetchModelEstimate` params 类型同步加,`estimateParams` 从 `displayRefs.length` / `data.<field>` 读
- **新 backend_model 字段**:如果前端有 UI 展示需要同步更新(参考 §十二、供应商通道)

## 17.2 新增一个视频/语音/音乐模型

跟 §17.1 类似,差异点:

| 模态 | defs.py 路径 | 计费 mapper 路径 | estimate_cost 读取字段 |
|---|---|---|---|
| 视频 | `models/video/defs.py` | `utils/mappers/<model>_billing.py` | `request.resolution` / `request.duration` / `request.video_refs` |
| 语音 | `models/speech/defs.py` | `utils/mappers/speech_billing.py` 或 `extra_billing.py` | `request.prompt` (按字节/字符) |
| 音乐 | `models/music/defs.py` | 一般固定积分,无 mapper | — |
| ASR | `models/asr/defs.py` | `utils/mappers/fishaudio_asr_billing.py` 等 | `request.audio_refs` (按音频时长) |

视频模型特别注意:
- **duration 默认 5 秒,上限按 model schema 强制**(Seedance 2.0 是 4~15)
- **video_refs 占位用 `[object()] * N`**(`estimate_model_points:536`),不要传真实视频 bytes
- 详见 §15.4 bug #2 / §16.3 表

## 17.3 新增一个全新 backend(全新上游 API)

详见 §九、§四 —— 需要写:
- `RH_ComfyUI/utils/backends/<vendor>/api.py`(Adapter)
- `RH_ComfyUI/utils/backends/<vendor>/channel.py`(ProviderChannel,可选)
- `core/channels/channel.py` 注册到 `ChannelBinding`
- `models/<modality>/defs.py` 加模型类,`backend="<vendor>"`
- 配置文件加 `<vendor>_apikey` 等

**2026-08 异步 backend 额外必做**(详见 [§二十](./20-cancel-resume-and-wire-audit.md)):
1. 上游 create 返回 task_id 后 → `bind_vendor_cancel(vendor_task_id, cancel_remote, channel_name)`
2. 真正 POST 前 → `set_wire_from_http_body(mask_body(body))`(统计最终 prompt/body)
3. 若支持远程 cancel → 模型/通道 `supports_remote_cancel=True`(诚实声明)
4. 若可仅凭 task_id poll → 考虑接入 `resume.py` 分支 + `can_resume`
5. 测:`test_cancel_generation` 模式 / wire 单测 / 契约 golden

## 17.4 改计费曲线

| 改的内容 | 改的文件 | 测试 |
|---|---|---|
| 单价 / 档位 token 数 | `utils/mappers/<model>_billing.py` 常量 | `tests/test_<model>_billing.py` |
| 费率公式 | `utils/mappers/<model>_billing.py` 函数 | `tests/test_<model>_billing.py` |
| `point_cost` 静态值 | `models/<modality>/defs.py` `node_def()` | 手工 curl `/models` 验证 |
| `point_range` max/min 输入 | `models/<modality>/defs.py` `point_range()` | `tests/test_dynamic_estimate_trigger.py` |

## 17.5 改 schema(添加/修改/删除字段)

**修改 schema 等于改前端契约**,影响面:

```
input_schema.images.max_items
       ↓
model.accepts_images / max_input_images
       ↓
前端 catalog 展示
       ↓
estimate_cost 的 supports() 判断
       ↓
estimate API 是否接受 num_input_images
```

**改动清单**:
1. `models/<modality>/defs.py` `node_def()` 改 `inputs`
2. **确认 supports()**(`base/generation.py`)对 inputs 字段的判定逻辑(参考同模态其他模型)
3. **确认 estimate_cost** 读这个字段(若 schema 暴露,estimate_cost 也应该读)
4. **确认 estimate API** 透传这个字段(`estimate_model_points` 签名 + 路由 handler)
5. **前端同步**:`GenerationNode.estimateParams` 加读取 + `fetchModelEstimate` 加类型
6. **测试**:更新 `tests/test_model_schema.py` 契约快照(如有)
7. **前端 e2e**:浏览器实测 schema 渲染 + estimate 响应

## 17.6 闭源接入模型(另外的兼容插件生态)

参考 §七、闭源插件接入。**不要在开源仓库留任何条件分支**。

```python
# 闭源插件里
from RH_ComfyUI.core.routing.registry import model_registry, AIGCGenerationBase

class ClosedSourceModel(AIGCGenerationBase):
    def __init__(self):
        super().__init__(self.node_def())
    @staticmethod
    def node_def():
        return NodeDef(name="closed_model", ...)
    def estimate_cost(self, request):
        # 闭源自己的计费逻辑(可能调外部 API 查实时价格)
        ...
    def point_range(self):
        return (1, 100)

model_registry.register(ClosedSourceModel())
```

## 17.7 提交前自检清单(2026-07 后必跑;2026-08 取消/wire 增项)

- [ ] `pytest tests/ -q` 全绿(允许 pre-existing 失败)
- [ ] `ruff check RH_ComfyUI`
- [ ] `pytest tests/test_dynamic_estimate_trigger.py` — 动态模型 `min < max`
- [ ] `pytest tests/test_ratio_size_map_correctness.py` — _RATIO_SIZE_MAP 单调性
- [ ] `pytest tests/test_http_contract.py` — ModelEntry 字段契约快照(含 cancel 字段)
- [ ] `pytest tests/test_cancel_generation.py tests/test_statistics_request_body.py` — 取消/wire
- [ ] 异步模型:真实跑一单 → 查记录 `prompt`/`request_body_json` 是否为**改写后**上游形态
- [ ] 异步模型:进行中 cancel → status=cancelled、不双退
- [ ] `/models` 中 `supports_remote_cancel` 与真实 API 一致(`rh_app`=false)
- [ ] 浏览器实测:选新模型 → schema → estimate 实时更新
- [ ] 手动 `curl /models/estimate?model=<name>&...` 验证新参数透传

## 17.8 常见出错模式

| 现象 | 根因 | 修复 |
|---|---|---|
| 前端不调 estimate,显示固定积分 | `point_range` min == max | 用更长 max 输入 |
| 前端切 image_size 积分不变 | estimate_cost 读 key 与 API 写 key 不一致 | 改成同 key + `or` 兼容 |
| 4K 反而比 2K 便宜 | `_RATIO_SIZE_MAP` cell 比例错 | 按 OpenAI 4 条硬约束重建 |
| 前端显示 quality 控件但切换无效 | schema 暴露了计费不区分的字段 | 从 schema 移除 |
| 输入多张图积分不变 | `estimate_model_points` 没传 `num_input_images` | 加 `images=[b""] * N` 占位 |
| FastAPI 422 拒绝 | estimate API 签名缺参数 | 加 Query 参数 |
| dispatch 报 "积分不足" | `estimate_cost` 返回值 > 用户余额 | 改 billing mapper 或减输入 |
| 显示 is_dynamic=false | `estimate_cost` 返回值 == `point_cost` | 检查 `point_range` 覆盖是否正确 |
| 消费页 prompt 无【图片N】/ 仍是 [参考图片N] | 未 set_wire 或 record 未 UPDATE | §二十 §20.4 |
| rh_app 显示可远程取消 | 通道 remote 计算错误 | §二十 §20.2 |
| 重启后无法 resume | 未 bind_vendor_cancel / 无 vendor_task_id | §二十 §20.3 |

## 17.9 上线后 7 天监控

1. **统计落库**:查 `RHComfyuiTaskRecord`,确认新模型 `status=ok` 比例正常
2. **estimate 误差**:对比 `task.point_cost` 与实际扣费的差额(应当 < 1 积分/任务)
3. **前端 422 日志**:浏览器 console / Network 面板,看是否还有未透传的参数
4. **rate 投诉**:用户报告"积分不准"时,先看 §15.5 自检清单 5 条都过了没