# core/schema — 数据契约层

内核最底层,零内部依赖。所有跨层传递的数据结构都在这里定义。

| 文件 | 内容 |
|---|---|
| `types.py` | `PortType` / `PortSpec`(端口 schema)、`MediaRef`、`ContentItem`、`NodeOutput`、`ProgressEvent`、`ProgressCallback` |
| `request.py` | `TaskType`、`GenerationRequest`、`GenerationResult` |
| `card.py` | `ModelCard` — 模型自描述元数据(简介/强项/品类/费用说明),服务 Agent 智能选型、HTTP 清单 `card` 字段与 AI 知识库 |

## 维护须知

- 这些类型同时被 HTTP 契约(`/api/RH_ComfyUI/models`)序列化暴露,**字段只增不改不删**;
- 旧代码从 `utils/core/types.py` / `request.py` import 的同名类型是同一批定义
  (utils 侧为兼容 shim),不要在两处重复定义;
- 新增字段一律带默认值,保证旧调用方无感。
