---
name: rh-comfyui-development
description: >
  当用户要求"给 RH_ComfyUI 加一个模型"、"新增一个生图/生视频/TTS/音乐模型"、
  "接一家新供应商/渠道"、"改模型参数面/校验规则"、"调整积分/计费"、
  "查生成统计/退款问题"、"画布模型清单接口怎么改"、"闭源插件怎么接入"、
  "怎么覆盖开源模型"、"dispatch 流程是什么"、"负载均衡/熔断怎么配"、
  "参考音频怎么接"时触发此 SKILL。
  对所有 RH_ComfyUI 插件的开发与维护任务都应优先读取此 SKILL。

  RH_ComfyUI 是 gsuid_core 的 AIGC 统一生成插件(图片/视频/音乐/数字人语音)。
  2026-07 起采用基于抽象基类(ABC)的全编程式架构:模型定义在
  models/*/defs.py 的 Python 类里(无 YAML),三大入口(命令 / AI Agent /
  canvas HTTP)统一走 core.dispatch.dispatch() 执行,计费(预留-提交-退款)、
  负载均衡(多通道熔断切换)、统计落库(RHComfyuiTaskRecord)在该点强制拦截。
  闭源插件通过 model_registry.register() 或 pip entry points
  (rh_comfyui.models 组)接入,开源仓库零闭源代码。
---

# RH_ComfyUI 开发与维护完整指南(核心入口)

> 本 SKILL 按章节拆分为主入口 + `references/` 子文档。需要某专题细节时,
> 顺着下文相对路径按需读取对应文件,**不要**一次性把所有内容塞进上下文。

## 文档目录索引

| 章节 | 主题 | 链接 |
|------|------|------|
| 一 | 架构总览(分层图、目录地图、一次生成的完整数据流、双注册表) | [references/01-architecture-overview.md](./references/01-architecture-overview.md) |
| 二 | core 内核(ABC 生命周期、schema 类型、错误族、公开接口边界) | [references/02-core-kernel.md](./references/02-core-kernel.md) |
| 三 | 新增/修改模型(defs.py 范式、PortSpec、NodeDef、跨字段校验、知识库) | [references/03-adding-models.md](./references/03-adding-models.md) |
| 四 | 通道与供应商(ProviderChannel、多通道负载均衡、熔断、新增一家供应商) | [references/04-channels-and-providers.md](./references/04-channels-and-providers.md) |
| 五 | 计费与统计(BillingPolicy 三件套、dispatch 失败语义、统计表字段) | [references/05-billing-and-telemetry.md](./references/05-billing-and-telemetry.md) |
| 六 | 三大入口(命令 / AI Agent / canvas HTTP、DispatchContext 构造、HTTP 契约红线) | [references/06-entry-points.md](./references/06-entry-points.md) |
| 七 | 闭源插件接入(注册途径、覆盖开源模型、私有数据隔离、独立钱包) | [references/07-closed-source-extension.md](./references/07-closed-source-extension.md) |
| 八 | 测试、代码红线与上线自查清单 | [references/08-testing-and-redlines.md](./references/08-testing-and-redlines.md) |
| 九 | 后端 Adapter、映射器与配置体系(六后端、Seedance Provider 子层、SERVICE/PLUGIN_CONFIG 全键) | [references/09-backends-and-config.md](./references/09-backends-and-config.md) |
| 十 | 命令清单与数据库(触发词、to_ai、RHBind、RHComfyuiTaskRecord 全列) | [references/10-commands-and-database.md](./references/10-commands-and-database.md) |
| 十一 | 凭证热更新(中途改 key 不重启)— `@property` / `refresh_config` / `update_credentials` 三种写法 | [references/11-credential-hot-reload.md](./references/11-credential-hot-reload.md) |
| 十二 | 供应商通道 / Gemini 生图 / 能力一致性 — 单层负载均衡、AdapterChannel 翻错、/models 可用性、Gemini SDK 双模、图在 steps、input_schema 与能力同步、计费退款 | [references/12-provider-channels-and-gemini.md](./references/12-provider-channels-and-gemini.md) |
| 十三 | OpenAI 兼容供应商池(网页配置零代码挂供应商、`OpenAI_Image_Providers`、`rh 刷新供应商`、resync 语义) | [references/13-openai-provider-pool.md](./references/13-openai-provider-pool.md) |

## 快速决策表(先看这里)

| 你要做的事 | 走哪条路 | 详见 |
|---|---|---|
| 加一个参数面简单的新模型(复用现有 backend) | `models/<模态>/defs.py` 加一个类 + 追加 `ALL_MODELS` | 三 |
| 加一个有跨字段约束的模型 | defs 类 + `overrides.py` 校验类 | 三 |
| 加一个全新执行链的模型(新 HTTP 上游) | backends 客户端 + ProviderChannel + 继承模态 ABC + `@register_model` | 三、四 |
| 给现有模型加一家供应商 | `channel_bindings()` 追加 `ChannelBinding` | 四 |
| 改模型积分价格 | defs 类的 `point_cost`(node_def 里) | 三 |
| 排查"扣了积分没出图" | dispatch 失败语义 + 统计表 `status/refunded` | 五 |
| 前端画布要新字段 | HTTP 契约只增不改,`ModelEntry` 加带默认值的字段 | 六 |
| 写闭源 / 另外的兼容插件生态模型 | 独立插件 `model_registry.register()` 或 entry points | 七 |
| 接一个全新上游 API | backends 新 Adapter(或 ProviderChannel)+ 配置键 | 九、四 |
| 加/改一个图片供应商(如 Gemini)/ 给模型加第二家供应商 | ProviderChannel + `channel_registry.register_binding` | 十二、四 |
| 给图片模型挂一家 OpenAI 兼容供应商(如千帆,零代码) | 网页控制台 `OpenAI_Image_Providers` + `rh 刷新供应商` | 十三 |
| 改模型能不能传参考图 / 参考图上限 | defs 的 `images` 端口(有无 + `max_items`),需与 `supported_shapes`/`supports_edit` 同步 | 十二、三 |
| 改请求组装 / workflow 注入 | `utils/mappers/` 对应函数 | 九 |
| 改命令触发词 / to_ai 文案 | rh_generate(触发词是兼容承诺,慎改) | 十 |
| 加统计维度 / 查积分逻辑 | RHComfyuiTaskRecord / RHBind | 十、五 |

## 三条最高红线(违反必被打回)

1. **不要绕过 `core.dispatch.dispatch()` 直接调 `model.run()`** —— 计费与统计会丢。
2. **`check_available()` / `validate()` 禁止网络请求与副作用** —— 路由阶段会批量调用。
3. **开源仓库零闭源内容** —— 不写另外的兼容插件生态的 URL、不写条件 import、不留按来源分叉的条件分支。

## 验证命令

```bash
# 在插件根目录(<gsuid_core 仓库>/gsuid_core/plugins/RH_ComfyUI)
# 用 gsuid_core 的 venv 解释器跑(系统 Python 的 fastapi/starlette 版本可能不配套)
python -m pytest tests/ -q        # 内核单测,全离线,必须全绿(2026-07-10: 73 passed)
ruff check RH_ComfyUI             # 代码风格
```
