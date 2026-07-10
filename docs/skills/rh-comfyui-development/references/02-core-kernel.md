# 二、core 内核

## 2.1 公开接口边界

`RH_ComfyUI/core/__init__.py` re-export 的符号是**唯一稳定接口**;
闭源/外部代码只允许 `from RH_ComfyUI.core import ...`,不深入子模块。
开源内部代码可以用子模块路径,但新增公开符号必须同步加进顶层 `__all__`。

注意一个坑:`from .dispatch import dispatch` 会把包属性 `dispatch` 覆盖为
同名函数,需要拿子模块对象时用
`importlib.import_module("RH_ComfyUI.core.dispatch.dispatcher")`。

## 2.2 AIGCGenerationBase 生命周期(模板方法)

`core/base/generation.py`。`run()` 固定生命周期,子类覆盖钩子:

```
run(request)
  ├─ 1. validate(request)            # schema 通用校验 + 子类跨字段校验
  ├─ 2. normalize(request)           # 默认值填充/单位归一化(可覆盖)
  ├─ 3. balancer.order_candidates()  # 负载均衡排序通道
  ├─ 4. execute_on_channel(...)      # ★ 子类核心
  │     ├─ ChannelError(transient=True,如 429/503)→ 原通道退避重试一次
  │     │   (间隔 transient_retry_delay=2s,不计熔断;仅补试一次)
  │     └─ ChannelError(retryable=True)→ 记熔断 → 换下一通道
  └─ 5. postprocess(output)          # 输出归一化(可覆盖)
```

同通道退避重试的动机:切换通道意味着整单重新生成(重复烧钱),而 429/503
是"通道健康、瞬时过载",原地等一下更省;重试仍失败才走常规切换。

子类必须实现三个抽象方法:

| 方法 | 职责 |
|---|---|
| `input_schema() -> dict[str, PortSpec]` | 编程式声明输入端口(驱动校验/前端表单/Agent 文档) |
| `channel_bindings() -> list[ChannelBinding]` | 声明可执行通道(1 个=单通道,多个=负载均衡) |
| `execute_on_channel(request, binding, *, on_progress)` | 在指定通道上执行一次生成 |

关键类属性:`name`(主键,慎改)/ `display_name` / `modality` / `card`
(ModelCard,Agent 选型依据)/ `point_cost` / `priority` / `execution_mode`
(sync/async_poll)/ `max_concurrency`(0=只受全局闸)/ `required_config`
(可用性所需配置键,缺失自动标不可用)。

可覆盖钩子:`validate()`(必须先 `super().validate()`)、`normalize()`、
`estimate_cost(request) -> int`(**动态计费**:dispatcher 用它确定预扣金额,
默认恒等于 `point_cost`;按参数分档计费——如视频按分辨率×时长——覆盖本方法,
必须是纯函数、不做 IO)、`supports()`(路由的输入档案匹配)、
`output_schema()`(多输出模型覆盖)、`check_available()` / `unavailable_reason()`、
`postprocess()`。

**设计约束**:本类不做计费/统计/全局限流(dispatcher 的职责),
不持有 HTTP 细节(通道/backends 的职责)。

## 2.3 四大模态基类

| 类 | 额外能力字段 | 额外校验 |
|---|---|---|
| `VideoGenerationBase` | `supported_shapes`(VideoTaskShape 集合)/ `supported_resolutions` / `supported_ratios` / `supports_generate_audio` | 形态推断 + 分辨率/宽高比/有声开关约束 |
| `DigitalHumanSpeechBase` | `supports_voice_clone` / `supports_mood` / `builtin_voices` / `has_default_voice`;`base_speech_schema()` 提供含 `reference_audio` 的骨架 schema | 参考音频与音色回落 |
| `ImageGenerationBase` | `supports_edit` / `max_input_images` | — |
| `MusicGenerationBase` | `supports_lyrics` | — |

`VideoTaskShape`:`TEXT2VIDEO / IMAGE2VIDEO / FIRST_LAST_FRAME / MULTIMODAL`。

`VideoGenerationBase.normalize()` 统一完成视频预处理:分辨率小写归一 +
`images` 与 `ordered_content` 图片项的等比缩放(最长边 ≤ 800px,EXIF 校正)。
入口层(api.submit / bot 命令)**不再各自预处理** —— run() 是唯一执行路径,
此处天然覆盖三入口(回归测试 `tests/test_video_normalize.py`)。

## 2.4 schema 类型(core/schema/)

| 类型 | 说明 |
|---|---|
| `GenerationRequest` | 统一请求:prompt / images / video_refs / audio_refs / reference_audio / ratio / resolution / duration / seed / params(自由字典)等 |
| `PortSpec(type, required, default, values, min_items, max_items, minimum, maximum, item_type, title, description)` | 单端口声明;title=前端配置面板短标题(几个字),description=完整说明(Agent 消费,前端缺 title 时回退用它) |
| `PortType` | TEXT/INTEGER/NUMBER/BOOLEAN/ENUM/LIST/IMAGE/AUDIO/VIDEO/CONTENT/OUTPUT_* |
| `NodeOutput` | 模型执行产物(data/mime_type/outputs/usage/metadata) |
| `GenerationResult` | dispatch 返回给入口的最终结果(含 model_used/cost_points) |
| `ModelCard` | description/strengths/categories/weaknesses 等自描述,Agent 智能选型与 HTTP `card` 字段数据源 |

这些类型被 HTTP 契约序列化暴露,**字段只增不改不删,新增必须带默认值**。

## 2.5 错误族(core/base/errors.py)

| 异常 | 语义 | 计费后果 |
|---|---|---|
| `ValidationError` | 参数校验失败(message 面向用户,写人话) | 不扣费 |
| `ModelUnavailableError` | 路由无可用模型 | 不扣费 |
| `BillingDeniedError` | 积分不足等 | 不扣费 |
| `ChannelError(msg, retryable=bool, transient=bool)` | 通道执行失败;retryable=True 时 run() 自动切下一通道并记熔断;transient=True(429/503 类瞬时错误)先在原通道退避重试一次、不计熔断 | 全部通道失败则退款 |
| `AllChannelsFailedError` | 所有通道失败 | 退款 |

## 2.6 路由(core/routing/router.py 的 route())

五级策略:① 用户显式 model(精确→同模态模糊+supports 过滤)→
② 输入档案 supports() 匹配 → ③ check_available() 过滤 →
④ AI 推荐钩子(可选,失败静默降级)→ ⑤ priority 最高组内随机。
