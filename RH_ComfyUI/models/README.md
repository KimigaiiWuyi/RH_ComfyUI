# models — 开源模型实现层(全编程式)

自 2026-07 起,**模型定义不再使用 YAML**:每个模型是一个 Python 类,
定义在各模态的 `defs.py` 里,继承模态桥接类/编程式类(最终都继承 core 的 ABC)。
启动时 `discover_builtin_models()`(由插件 `on_core_start` 调用)实例化全部
模型类,注册进 `core.routing.model_registry`,同时把各自的 `NodeDef` 注册进
`pipeline_registry`(Adapter 执行 / AI 知识库 / HTTP 契约继续消费 NodeDef,
数据源改为代码)。

| 文件/目录 | 内容 |
|---|---|
| `bridge.py` | `AdapterChannel`(旧 Adapter → ProviderChannel 适配)、四个模态桥接基类(从 NodeDef 推导能力) |
| `image/defs.py` | 7 个图片模型定义类 |
| `music/defs.py` | 1 个音乐模型定义类 |
| `speech/defs.py` | 3 个语音模型定义类;`overrides.py` 有 `IndexTTS2Model`(参考音频一等公民) |
| `video/defs.py` | 4 个视频模型定义类;`overrides.py` 有 `SeedanceVideoModel` / `Wan22VideoModel`(跨字段校验范本) |
| `__init__.py` | `discover_builtin_models()` |

## 新增模型

在对应模态的 `defs.py` 加一个类(参考现有类):

1. 继承模态桥接类(`ImagePipelineModel` 等,沿用 Adapter 执行链)或
   overrides 里的编程式类;
2. `node_def()` 用代码声明身份 / 端口(PortSpec)/ 映射(mappings 或
   mapper_func)/ 能力(CapabilityManifest);
3. 追加到文件末尾的 `ALL_MODELS` 列表,重启即生效(命令/Agent/HTTP 三入口自动可见);
4. 有跨字段约束时覆盖 `validate()`(先 `super().validate()`),并在
   `tests/test_schema_validation.py` 补用例。

## 维护须知

- 本目录只放**开源**模型;闭源模型通过 pip entry points(组名
  `rh_comfyui.models`)或外部插件直接调 `model_registry.register()` 接入;
- `knowledge_content` 字段是 AI 知识库的数据源,修改模型能力时同步更新;
- 修改任何 defs 后跑 `python -m pytest tests/ -q` 与 `ruff check RH_ComfyUI/models`。
