# AGENTS.md

> 本文件遵循 [AGENTS.md](https://agents.md/)：给编码 Agent 的仓库说明（README for agents）。
> 人类用户说明见 [README.md](./README.md)。**源码是唯一事实源**。
>
> 加模型 / 计费 / 通道 / HTTP 契约：按需读
> [`.agents/skills/rh-comfyui-development/SKILL.md`](.agents/skills/rh-comfyui-development/SKILL.md)，
> **不要**一次把所有 `references/` 塞进上下文。

本仓库是 **GsCore 业务插件**，独立 git。放到 `gsuid_core/plugins/RH_ComfyUI/` 安装，不要跟框架一起提交。

## Project overview

AIGC 统一生成：图片 / 视频 / 音乐 / TTS / ASR。2026-07 起全编程式（`models/*/defs.py`，无 YAML）。

- 命令 / AI Agent / HTTP **只走** `core.dispatch.dispatch()`。计费、限流、统计在该点拦截。
- 开源仓库零宿主耦合：不写宿主 URL、不条件 import 宿主包。
- `Plugins(name="RH_ComfyUI", force_prefix=["rh", "cf", "RH"], allow_empty_prefix=False)`。
- 版本：`RH_ComfyUI/version.py`（与 `pyproject.toml` `[project]` 均为 `2.0.0`）。Python `>=3.10,<4.0`。

## Repository map

```
.
├── AGENTS.md / README.md / LICENSE / ICON.png / Header.png
├── pyproject.toml / ruff.toml / pyrightconfig.json
├── __init__.py / __nest__.py
├── tests/                                      # 全离线 pytest
├── .agents/skills/rh-comfyui-development/
└── RH_ComfyUI/
    ├── __init__.py                             # Plugins + 显式 import 子包 + on_core_start
    ├── __full__.py / version.py / api.py       # 外部 submit / cancel / resume
    ├── core/                                   # 内核（每子目录有 README）
    │   ├── dispatch/                           # dispatch() 唯一执行路径
    │   ├── routing/                            # ModelRegistry、五级路由、LoadBalancer
    │   ├── billing/                            # reserve / commit / refund / settle
    │   ├── channels/                           # ProviderChannel、熔断、resync
    │   ├── base/                               # 模态 ABC + 错误族 + emotion
    │   ├── schema/  telemetry/  media_host.py
    ├── models/                                 # 内置模型
    │   ├── image/ video/ speech/ music/ asr/   # 各 defs.py + 可选 overrides.py
    │   └── bridge.py
    ├── rh_generate/                            # 命令 + to_ai + 知识库
    ├── rh_models/                              # FastAPI /api/RH_ComfyUI/models*
    ├── rh_agent/  rh_admin/  rh_help/  rh_config/
    └── utils/
        ├── backends/                           # comfyui / gemini / seedance / …
        ├── mappers/                            # 请求 → 厂商 payload / billing 常量
        ├── core/                               # 旧 NodeDef / pipeline 兼容层
        ├── database/                           # RHBind / RHComfyuiTaskRecord / 缓存表
        ├── resource/workflow/                  # Comfy workflow JSON
        └── module_identity.py / ai_tools.py / image_process.py / …
```

内层 `__init__.py` **显式** import：`core, utils, models, rh_help, rh_admin, rh_agent, rh_models, rh_generate`，再 `unify_module_identity`。`rh_config` 通过 `PLUGIN_CONFIG` / `SERVICE_CONFIG` 装载。

## Skills

| 任务 | 读 |
|------|-----|
| 本插件（模型 / 渠道 / 计费 / 契约） | [rh-comfyui-development](.agents/skills/rh-comfyui-development/SKILL.md) |
| 通用插件 SV / `to_ai` / 库表 | Core [gscore-plugin-development](../../../.agents/skills/gscore-plugin-development/SKILL.md) |
| 代码红线（编号条文 + 正反例） | Core 根 [`AGENTS.md`](../../../AGENTS.md) §1–§4、§1.9 |

单独 clone 时打开宿主 Core 仓库的 `AGENTS.md`。`.agents/skills/` 是给编码 Agent 的；运行时 Skill 不是这一套。

## Setup commands

在**本插件目录**执行。解释器指向 Core 仓库根 `.venv`。

```sh
uv run python -m pytest tests/ -q
uv run python -m pytest tests/test_cancel_generation.py tests/test_statistics_request_body.py tests/test_http_contract.py -q
uv run ruff check RH_ComfyUI tests
uv run ruff format --check RH_ComfyUI tests
```

`ruff.toml`：`line-length = 120`，`target-version = "py310"`，lint `E/F/I/W`。
`pyrightconfig.json` 的 `extraPaths` 按「本目录 = `gsuid_core/plugins/RH_ComfyUI`」指到 Core 根。
框架已有的 fastapi / pydantic / gsuid-core / sqlmodel **不要**再写进 `dependencies`。

## Code style

新代码与 Core 根 `AGENTS.md` **编号一致**，完整正反例以那份为准，不要在本文件另写一套。

| 编号 | 要求 |
|------|------|
| §1.1 | 禁止 try-except 兜底。例外：不可信外部输入；`_ai_return_xxx()` |
| §1.2 | 禁止 `cast()` |
| §1.3 | 禁止用 `type: ignore` 掩盖自身类型问题 |
| §1.4 | 禁止 `getattr` / `dict.get` 兜底；键存在则直接访问，否则 `isinstance` 收窄 |
| §1.5 | 类型标红：改类型或逻辑 → `A \| B` + `isinstance` → 最后才 assert |
| §1.6 | `#` 注释最多两行、每行 ≤88 字；只写为什么 / 坑 / 边界 |
| §1.7 | 不改 Core `system_prompt`；不砍 history 头；动态内容进 user 侧 |
| §1.8 | 禁止 `Any`（含 `dict[str, Any]`）；运行时变量类型必须可追踪 |
| §1.9 | 垂直能力只写本插件 `covers` / `aliases` / `capability_domain`，不写进框架词表 |
| §2 | 函数全标注；PEP 604 `X \| Y`；结构化 dict 用 TypedDict |
| §3 | 无 `__tablename__`；库方法 `@with_session`；比较用 `col()`；`rowcount` 先 `isinstance(..., CursorResult)` |
| §4 | 可能阻塞的 I/O 必须 `async`；CPU 重活 `to_thread` |

行宽 120。isort：`known-first-party = ["gsuid_core"]`（见本仓库 `ruff.toml`）。

本插件额外：

- 不绕过 `dispatch()` 调 `model.run()`。
- `check_available()` / `validate()` 零网络、零副作用。
- HTTP 契约只增不改（`tests/test_http_contract.py`）。
- 不在 `__init__` 冻结 `api_key` / `base_url`（`@property` / `refresh_config` / `update_credentials`）。
- `RHComfyuiTaskRecord` 既有列不改名不删；模型 `name` 是主键。
- 新目录带 `README.md`。注释与代码用「调用方 / 宿主 / 外部插件」。

允许的例外：Adapter 翻译 HTTP / 解析厂商 JSON；统计落库失败不打断生成。

## Testing

- `tests/`，pytest-asyncio。FakeModel + FakePolicy，内核单测不打网络。
- 统计用 `_mute_recording`。跨字段校验补 `test_schema_validation.py`。
- 新异步 backend：`bind_vendor_cancel` + `set_wire_*` 后 `record_task`。
- 改 HTTP 字段：更新 golden，不要删断言迁就实现。

## 本仓库结构约定

- 嵌套加载：`__nest__.py` + `__full__.py`。显式 import 防止第二棵模块树。
- 启动：`init_backends()` → `discover_builtin_models()` → `sync_openai_image_providers()` → 知识库 → 统计。
- 加模型：`models/<模态>/defs.py` + `ALL_MODELS`；跨字段约束写 `overrides.py`。
- 加供应商：`channel_bindings()` 或网页 `OpenAI_Image_Providers` + `rh 刷新供应商`。
- 配置每次调用再读。运行时在 `data/RH_ComfyUI/`，不进 git。

## 坑点

1. 绕过 dispatch → 扣费/统计丢失。
2. validate 打网络 → 路由批量调用打爆上游。
3. 空 key 冻在 `__init__` → `Illegal header value b'Bearer '`。
4. 三种 import 前缀必须 `unify_module_identity`。
5. 消费页 prompt 以最终 wire 为准（`set_wire_*`）。
6. `rh_app` ≠ `comfyui` 的 `supports_remote_cancel`。
7. 重启丢异步任务：`vendor_task_id` + `resume_poll`。
8. 上游只吃 `ratio` 的模型禁止假装暴露 `width`/`height`。

## Security notes

- API key / 积分账户只走配置与数据库，不进 git、不打日志全文。
- 统计 `request_body` 脱敏。预扣失败不得生成；取消路径禁止双重退款。
- 公网 Core：`WS_TOKEN` / WebConsole 鉴权。本插件 HTTP 挂同一 FastAPI。
