# 一、架构总览

## 1.1 一句话

所有 AIGC 生成(图片/视频/音乐/数字人语音)由**模型类**(继承 ABC)自己声明
参数面、校验规则与执行通道;三大入口统一走 `core.dispatch.dispatch()`,
计费、限流、统计在这一个点强制拦截。

## 1.2 分层图

```
┌── 入口层(构造 DispatchContext,不含业务逻辑)──────────────────────┐
│ rh_generate/(命令 + to_ai 桥接 Agent)  api.py(HTTP)      │
│ rh_models/(GET /api/RH_ComfyUI/models* 模型清单)                     │
└─────────────────────────────┬─────────────────────────────────────┘
                              ▼
┌── core/dispatch — 唯一执行路径 ────────────────────────────────────┐
│ route → validate(不扣费)→ reserve(estimate_cost 动态计费)      │
│       → [超时预算 Dispatch_Timeout] 并发闸 + model.run()          │
│       → commit / refund(幂等)→ record_dispatch(统计落库)      │
└─────────────────────────────┬─────────────────────────────────────┘
                              ▼
┌── core/ 内核(开源/闭源边界,只从顶层 __init__ import)────────────┐
│ routing/  ModelRegistry + route() 五级路由 + LoadBalancer(熔断) │
│ billing/  BillingPolicy(reserve/commit/refund)                  │
│ base/     AIGCGenerationBase + 四大模态 ABC + 错误族              │
│ channels/ ProviderChannel / ChannelBinding / LocalChannel         │
│ schema/   GenerationRequest / NodeOutput / PortSpec / ModelCard   │
│ telemetry/ record_dispatch(RHComfyuiTaskRecord 落库)            │
└─────────────────────────────┬─────────────────────────────────────┘
                              ▼
┌── models/ — 模型实现层(全编程式,无 YAML)────────────────────────┐
│ {image,music,speech,video}/defs.py:每模型一个类,node_def() 声明 │
│ bridge.py:NodeDef+Adapter 执行链 → ABC 的桥接基类                │
│ */overrides.py:跨字段校验类(Seedance / Wan / IndexTTS2)        │
└─────────────────────────────┬─────────────────────────────────────┘
                              ▼
       utils/backends/(HTTP 客户端与 Adapter,通信细节全部在这里)
```

依赖方向只允许自上而下;`core/` 内部为
`dispatch → routing / billing / telemetry → base → channels → schema`。

## 1.3 目录地图

| 路径 | 内容 | 什么时候动它 |
|---|---|---|
| `RH_ComfyUI/core/` | 生成引擎内核(ABC/路由/计费/调度/统计) | 加通用机制时;每个子目录有 README |
| `RH_ComfyUI/models/` | 内置模型定义(defs.py)与桥接层 | 加/改模型时(最常改) |
| `RH_ComfyUI/utils/backends/` | 各上游的 HTTP 客户端与 Adapter | 接新上游时 |
| `RH_ComfyUI/utils/mappers/` | 请求 → 厂商 payload / workflow 的映射函数 | 改某模型的请求组装时 |
| `RH_ComfyUI/utils/core/` | NodeDef/PipelineRegistry/旧 router(兼容层) | 一般不动 |
| `RH_ComfyUI/rh_generate/` | 命令入口(rh 生图/生视频/...)+ to_ai | 改命令交互时 |
| `RH_ComfyUI/rh_models/` | HTTP 模型清单(/api/RH_ComfyUI/models*) | 改清单展示时(契约只增不改) |
| `RH_ComfyUI/rh_agent/` | AgentNode 注册(rh_aigc_agent,AIGC 创作代理身份核 prompt) | 改 Agent Mesh 里的代理行为时 |
| `RH_ComfyUI/rh_admin/` | 积分管理命令 + `刷新供应商` + @ai_tools 管理工具 | 改积分/记录/供应商池命令时 |
| `RH_ComfyUI/api.py` | 外部插件 调用的编程接口(submit 等) | 改调用方对接时 |
| `RH_ComfyUI/utils/database/` | RHComfyuiTaskRecord 统计表 | 加统计维度时 |
| `tests/` | 内核单测(全离线) | 每次改 core/models |
| `.agents/skills/rh-comfyui-development/` | 本 SKILL(架构事实来源) | 架构级变更时同步 |

## 1.4 一次生成的完整数据流(调用方参考音频为例)

1. 调用方 `GET /api/RH_ComfyUI/models` → IndexTTS2 的 `input_schema` 含
   `reference_audio` 端口 → 调用方展示音频连线口;
2. 调用方提交 → `api.submit()` 组装 `GenerationRequest`(reference_audio=MediaRef)
   与 `DispatchContext`(policy=ExternalPrepaidPolicy, entry_point="http");
3. `dispatch()`:route 命中 IndexTTS2 → validate(schema 校验,不扣费)
   → reserve(外部预付只记账)→ `model.run()`;
4. `run()`:负载均衡选通道 → `execute_on_channel()` 经桥接层调 Adapter,
   参考音频注入 workflow → `NodeOutput`;
5. commit → `record_dispatch()` 写 RHComfyuiTaskRecord
   (user_id / model_name / prompt / point_cost / entry_point / channel)。

命令与 Agent 入口只是第 2 步不同:`rh_generate` 用 `PointsBillingPolicy`
(真实扣积分);Agent 经 `to_ai` 触发器桥接复用同一命令函数。

## 1.5 双注册表(model_registry 与 pipeline_registry)

- `core.routing.model_registry`:模型实例(ABC 子类),路由/dispatch 的数据源;
- `utils.core.pipeline.pipeline_registry`:NodeDef,被 Adapter 执行、AI 知识库
  (knowledge_content)与 HTTP 清单消费。

**两者都由 `models.discover_builtin_models()` 在启动时从代码填充**
(每个模型类的 `node_def()`),不再有 YAML 装载路径;pipeline_registry 现在是
model_registry 的**派生只读视图**(带 `.node` 的模型自动出现,不再单独注册)。
写不依赖 Adapter 的新模型时可以只进 model_registry(用 `@register_model`),
无需 NodeDef —— 这类"纯编程式模型"(`model.node is None`)自 2026-07-10 起也会
出现在 HTTP 清单(`build_model_catalog` 从 model_registry 补录),三入口可见性一致。

启动顺序(`RH_ComfyUI/__init__.py::init_pipeline_system`):
`init_backends()` → `discover_builtin_models()` →
`sync_openai_image_providers()`(供应商池,见 13 章,须在模型注册后)→
注册 AI 知识库 → 触发统计模块加载。
