# core/base — ABC 五件套与错误族

| 文件 | 内容 |
|---|---|
| `generation.py` | `AIGCGenerationBase` — 顶层 ABC:身份/元数据类属性、`input_schema()`、`validate()`(默认走 schema_validator)、`channel_bindings()`、`execute_on_channel()`、`check_available()` |
| `video.py` | `VideoGenerationBase` + `VideoTaskShape` 形态枚举;声明式能力字段(`supported_shapes` / `supported_resolutions` / `supported_ratios` / `supports_generate_audio`)+ 跨字段校验 |
| `speech.py` | `DigitalHumanSpeechBase` — 参考音频(`reference_audio`)为一等端口,`base_speech_schema()` 提供模态骨架 schema |
| `image.py` | `ImageGenerationBase` — 图生图/编辑能力声明 |
| `music.py` | `MusicGenerationBase` |
| `schema_validator.py` | 按 `input_schema()` 的 PortSpec 做通用校验(必填/枚举/数量上限) |
| `errors.py` | `GenerationError` 族:`ValidationError` / `ModelUnavailableError` / `ChannelError(retryable)` / `AllChannelsFailedError` / `BillingDeniedError` |

## 写一个新模型类的最小步骤

1. 继承对应模态基类,填 `name` / `display_name` / `point_cost` / `card`;
2. 实现 `input_schema()`(模型自己的参数面)与 `channel_bindings()`;
3. 实现 `execute_on_channel()`;跨字段约束覆盖 `validate()`(先 `super().validate()`);
4. 在 `models/__init__.py` 的 `CUSTOM_MODEL_CLASSES` 登记(桥接替换)或用
   `@register_model` 直接注册。

## 维护须知

- `check_available()` 只读配置,禁止发网络请求(路由阶段对每个候选都会调用);
- 校验永远发生在计费之前(dispatcher 保证),`validate()` 里不要有副作用。
