---
name: rh-comfyui-development
description: >
  当用户要求"给 RH_ComfyUI 加一个模型"、"新增一个生图/生视频/TTS/音乐模型"、
  "接一家新供应商/渠道"、"改模型参数面/校验规则"、"调整积分/计费"、
  "查生成统计/退款问题"、"调用方模型清单接口怎么改"、"外部插件怎么接入"、
  "怎么覆盖开源模型"、"dispatch 流程是什么"、"负载均衡/熔断怎么配"、
  "参考音频怎么接"、"取消生成任务"、"supports_remote_cancel"、
  "resume-poll / 启动恢复轮询"、"统计 prompt 不是最终上游请求"、
  "rh_app 和 comfyui 取消能力"时触发此 SKILL。
  对所有 RH_ComfyUI 插件的开发与维护任务都应优先读取此 SKILL。

  RH_ComfyUI 是 gsuid_core 的 AIGC 统一生成插件(图片/视频/音乐/数字人语音)。
  2026-07 起采用基于抽象基类(ABC)的全编程式架构:模型定义在
  models/*/defs.py 的 Python 类里(无 YAML),三大入口(命令 / AI Agent /
  HTTP)统一走 core.dispatch.dispatch() 执行,计费(预留-提交-退款)、
  负载均衡(多通道熔断切换)、统计落库(RHComfyuiTaskRecord)在该点强制拦截。
  2026-08 起:主动取消(cancel_generation + 上游 DELETE)、resume_poll、
  统计 prompt/request_body 以最终 wire 为准;rh_app≠comfyui 取消能力分离。
  外部插件通过 model_registry.register() 或 pip entry points
  (rh_comfyui.models 组)接入,开源仓库零宿主业务包依赖。
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
| 五 | 计费与统计(BillingPolicy、dispatch 失败语义、取消摘要、wire 摘要) | [references/05-billing-and-telemetry.md](./references/05-billing-and-telemetry.md) |
| 六 | 三大入口(命令 / AI Agent / HTTP、cancel/resume API、HTTP 契约红线) | [references/06-entry-points.md](./references/06-entry-points.md) |
| 七 | 外部插件接入(注册途径、覆盖内置模型、私有数据隔离、独立钱包) | [references/07-closed-source-extension.md](./references/07-closed-source-extension.md) |
| 八 | 测试、代码红线与上线自查清单 | [references/08-testing-and-redlines.md](./references/08-testing-and-redlines.md) |
| 九 | 后端 Adapter、映射器与配置体系(六后端、Seedance Provider 子层、SERVICE/PLUGIN_CONFIG 全键) | [references/09-backends-and-config.md](./references/09-backends-and-config.md) |
| 十 | 命令清单与数据库(触发词、to_ai、RHBind、RHComfyuiTaskRecord 全列) | [references/10-commands-and-database.md](./references/10-commands-and-database.md) |
| 十一 | 凭证热更新(中途改 key 不重启)— `@property` / `refresh_config` / `update_credentials` 三种写法 | [references/11-credential-hot-reload.md](./references/11-credential-hot-reload.md) |
| 十二 | 供应商通道 / Gemini 生图 / 能力一致性 — 单层负载均衡、AdapterChannel 翻错、/models 可用性、Gemini SDK 双模、图在 steps、input_schema 与能力同步、计费退款 | [references/12-provider-channels-and-gemini.md](./references/12-provider-channels-and-gemini.md) |
| 十三 | OpenAI 兼容供应商池(网页配置零代码挂供应商、`OpenAI_Image_Providers`、`rh 刷新供应商`、resync 语义) | [references/13-openai-provider-pool.md](./references/13-openai-provider-pool.md) |
| 十四 | 语音情绪体系与自动音色克隆(EmotionStyle 基类归一、内联/剥离/枚举收敛、参考音频持久去重、fish_tts 样例) | [references/14-speech-emotion-and-voice-clone.md](./references/14-speech-emotion-and-voice-clone.md) |
| 十五 | **积分计价规则总览**(公式推导、9 类计费曲线、改价流程、6 个已知 bug 列表、防踩指南) | [references/15-billing-pricing-formulas.md](./references/15-billing-pricing-formulas.md) |
| 十六 | **模型目录与 estimate API 契约**(`/models` 系列端点、input_schema 结构、point_range 双重作用、兼容性矩阵) | [references/16-models-catalog-api.md](./references/16-models-catalog-api.md) |
| 十七 | **新增/修改模型 与 计费改动 完整交接清单**(改文件顺序、跑测试、常见出错模式、提交前自检) | [references/17-adding-models-billing-handoff.md](./references/17-adding-models-billing-handoff.md) |
| 十八 | **图片像素量压缩**(上传/传输前瘦身、compress_to_max_pixels、1080P 阈值、格式保持、调用方接入) | [references/18-image-compression.md](./references/18-image-compression.md) |
| 十九 | **Seedance 2.5 与输入视频时长计费**(模型 seedance2.5、ark+gateway、费率、input_video_duration、task_mode、宿主透传) | [references/19-seedance25-and-input-duration.md](./references/19-seedance25-and-input-duration.md) |
| 二十 | **取消 / resume-poll / 最终 wire 落库**(cancel_generation、rh_app≠comfyui、vendor_task_id、resume_poll、wire_capture、开源边界) | [references/20-cancel-resume-and-wire-audit.md](./references/20-cancel-resume-and-wire-audit.md) |

## 快速决策表(先看这里)

| 你要做的事 | 走哪条路 | 详见 |
|---|---|---|
| 加一个参数面简单的新模型(复用现有 backend) | `models/<模态>/defs.py` 加一个类 + 追加 `ALL_MODELS` | 三 |
| 加一个有跨字段约束的模型 | defs 类 + `overrides.py` 校验类 | 三 |
| 加一个全新执行链的模型(新 HTTP 上游) | backends 客户端 + ProviderChannel + 继承模态 ABC + `@register_model` | 三、四 |
| 给现有模型加一家供应商 | `channel_bindings()` 追加 `ChannelBinding` | 四 |
| 改模型积分价格 | defs 类的 `point_cost`(node_def 里) | 三 |
| 排查"扣了积分没出图" | dispatch 失败语义 + 统计表 `status/refunded` | 五 |
| **取消进行中任务 / 上游 DELETE** | `cancel_generation` + `bind_vendor_cancel` + 模型 cancel 标志 | **二十、五** |
| **rh_app 能否取消 / 与 comfyui 区别** | 通道级 `supports_remote_cancel`;AI 应用无 cancel | **二十** |
| **进程重启后继续轮询上游结果** | `resume_poll` + `extra_params.vendor_task_id` | **二十** |
| **消费页 prompt 与上游不一致** | `wire_capture` 最终 body;backend 须 `set_wire_*` | **二十、五** |
| 调用方要新字段 | HTTP 契约只增不改,`ModelEntry` 加带默认值的字段 | 六、十六 |
| 写外部插件模型 | 独立插件 `model_registry.register()` 或 entry points | 七 |
| 接一个全新上游 API | backends 新 Adapter(或 ProviderChannel)+ 配置键 | 九、四 |
| 加/改一个图片供应商(如 Gemini)/ 给模型加第二家供应商 | ProviderChannel + `channel_registry.register_binding` | 十二、四 |
| 给图片模型挂一家 OpenAI 兼容供应商(如千帆,零代码) | 网页控制台 `OpenAI_Image_Providers` + `rh 刷新供应商` | 十三 |
| 开关通道/改 Slot 模型勾选后 registry 没跟上 | 网页 `set_config` 自动重绑;外部插件 `register_resync_hook` | 七、十三 |
| 加一个语音/TTS 模型 / 改某模型情绪风格(内联/枚举/自然语言) | overrides 声明 `emotion_style`,基类 `normalize()` 统一整形 | 十四 |
| 参考音频自动克隆 / 克隆结果持久复用去重 | mapper 复用 `RHVoiceCloneCache`(按内容哈希全局去重) | 十四 |
| 改模型能不能传参考图 / 参考图上限 | defs 的 `images` 端口(有无 + `max_items`),需与 `supported_shapes`/`supports_edit` 同步 | 十二、三 |
| 改请求组装 / workflow 注入 | `utils/mappers/` 对应函数 | 九 |
| 改命令触发词 / to_ai 文案 | rh_generate(触发词是兼容承诺,慎改) | 十 |
| 加统计维度 / 查积分逻辑 | RHComfyuiTaskRecord / RHBind | 十、五 |
| **改积分价格 / 加新计费维度 / 排查积分不准** | defs 的 `estimate_cost` / `point_range` + `utils/mappers/<model>_billing.py` 常量 | **十五** |
| **预扣后按供应商 usage 实扣(防双重扣费)** | `settle_cost` + `BillingPolicy.settle`;调用方差额对齐 | **五、十五、十九** |
| **历史 Seedance 2.x 按 raw usage 回算积分** | `reconcile_seedance_usage_billing` | **五** |
| **改 schema 字段 / 加新参数面 / 排查 estimate 失效** | defs 的 `node_def()` `inputs` + `rh_models/webapi.py` 路由 handler + `rh_models/api.py:estimate_model_points` | **十六、十七** |
| **调用方报"积分不变" / 不调 estimate / 422 / 4K 反便宜** | 跨引擎与调用方双侧排查 | **十五** |
| 上传/传输前压缩图片(4K→1080P、格式不变) | `RH_ComfyUI.utils.image_process.compress_to_max_pixels_async` | **十八** |
| **加/改 Seedance 2.5 / 输入视频时长影响积分** | defs `seedance2.5` + billing `input_video_duration` + 宿主透传 | **十九、十五** |
| **新异步 backend 接入** | create 后 bind_vendor_cancel + POST 前 set_wire + 可选 resume | **二十、四、九** |

## 三条最高红线(违反必被打回)

1. **不要绕过 `core.dispatch.dispatch()` 直接调 `model.run()`** —— 计费与统计会丢。
2. **`check_available()` / `validate()` 禁止网络请求与副作用** —— 路由阶段会批量调用。
3. **开源仓库零宿主业务耦合** —— 不写宿主 URL、不写条件 import 宿主包、
   不按产品来源分叉;宿主能力用扩展点注入(见 §七、§二十)。
   代码与注释用中性词「调用方 / 宿主 / 外部插件」,不暴露具体前端或宿主后端。

## 验证命令

```bash
# 在插件根目录(<gsuid_core 仓库>/gsuid_core/plugins/RH_ComfyUI)
# 用 gsuid_core 的 venv 解释器跑(系统 Python 的 fastapi/starlette 版本可能不配套)
python -m pytest tests/ -q        # 内核单测,全离线,必须全绿
# 取消 / wire / 契约相关子集:
python -m pytest tests/test_cancel_generation.py tests/test_statistics_request_body.py tests/test_http_contract.py -q
ruff check RH_ComfyUI             # 代码风格
```
