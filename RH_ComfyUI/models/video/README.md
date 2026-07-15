# models/video — 视频模态编程式模型

蓝图 §5.2 的范本:两个参数面严重不一致的模型在同一 ABC 下共存。

| 类 | 能力面 |
|---|---|
| `SeedanceVideoModel` | 多参考(图/视频/音频合计 ≤12)/首尾帧/单图/纯文本;480P~4K;16:9 等固定比例;有声开关 |
| `Wan22VideoModel` | 仅首尾帧/首帧/纯文本(**无多参考**);≤720P(像素积约束);任意宽高比;无有声配置 |

执行链复用桥接层(NodeDef + Adapter,零改动);差异全部体现在能力声明
(`supported_shapes` / `supported_resolutions` / `supported_ratios` /
`supports_generate_audio`)与 `validate()` 的跨字段约束里。

## 维护须知

- 新视频模型:继承 `VideoPipelineModel`(保执行链)或 `VideoGenerationBase`
  (全新执行),先 `super().validate()` 再加自己的约束;
- 校验错误信息面向最终用户(命令/调用方都会直接展示),写人话。
