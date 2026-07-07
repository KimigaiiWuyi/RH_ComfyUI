# 十、命令清单与数据库

## 10.1 插件注册与前缀

```python
Plugins(name="RH_ComfyUI", force_prefix=["rh", "cf", "RH"], allow_empty_prefix=False)
```

用户消息必须以 `rh` / `cf` / `RH` 开头(如 `rh 生图 一只猫`)。

## 10.2 命令清单

**生成命令(rh_generate/,均带 to_ai= 桥接为 AI 工具)**

| 触发词 | 任务 | 说明 |
|---|---|---|
| `生图` | image | 无图=文生图,带图=编辑/重绘 |
| `改图` / `编辑图片` / `图片编辑` | image | 必须带图 |
| `生视频` / `生成视频` | video | 形态按输入自动决定(0图=文生/1图=图生/2图=首尾帧/图+音视频=多模态) |
| `生音乐` / `生成音乐` | music | |
| `生语音` / `生成语音` | speech | 带音频=参考音频克隆;Agent 无音频时按 to_ai 指引先取 Persona 音频 |
| `模型列表`(fullmatch) | — | 模型清单 |
| `模型详情 <名>` | — | 单模型端口详情 |
| `通道状态`(fullmatch) | — | 各后端可用性 |

**积分与记录(rh_admin/)**

| 触发词 | 权限 |
|---|---|
| `增加积分` / `加积分`、`减少积分` / `扣积分` | 管理员 |
| `查询积分` / `查看积分`、`消费记录` / `我的记录` 等 | 用户 |
| `全员消费记录` / `全局记录` 等 | 管理员 |

另有 `rh_models/`(`模型列表`/`模型清单`/`可用模型` 命令 + HTTP 路由)、
`rh_help/`(`帮助` fullmatch)。管理类纯 AI 工具用 `@ai_tools`
(rh_admin/commands.py),与 to_ai 互斥。

改 to_ai 文案 = 改 Agent 的工具说明书,措辞影响 Agent 何时调用、怎么传参,
改动后要人工对话验证(触发词本身是兼容承诺,不要改)。

## 10.3 数据库(utils/database/)

**RHBind — 用户积分**(`Bind` 子类,`point` 默认 20 = `Default_Point`)。
接口:`add_point()` / `deduct_point()`(初次使用自动建档)。
计费永远经由 `PointsBillingPolicy` 调它,不要在业务代码直接扣。

**RHComfyuiTaskRecord — 生成流水**(每次 dispatch 一条,分组如下)

| 组 | 列 |
|---|---|
| 身份 | `user_id` / `bot_id` / `group_id` |
| 任务 | `task_type` / `task_name`(=模型 name)/ `backend` / `backend_model` / `backend_provider` |
| 参数 | `duration_seconds` / `width` / `height` / `ratio` / `resolution` / `seed` / `voice_id` / `extra_params_json` / `prompt`(≤4000) |
| 结果 | `status`(ok/failed/cancelled)/ `elapsed_ms` / `point_cost` / `refunded` / `error_message`(≤2KB)/ `raw_response_json`(≤64KB) |
| 追踪 | `trace_id` / `entry_point`(command/agent/http)/ `created_at` |

规则(同 05 章):既有列不改名不删;加列必须带默认值(gsuid_core
`exec_list` 自动迁移);写入只在 `core/telemetry/recorder.py` 与
`utils/database/statistics.py`,不要在别处散落 INSERT。

**统计查询**:`statistics.py` 提供按用户/全局的分页查询,供
`消费记录` / `全员消费记录` 命令与后续报表使用;表已注册到
gsuid_core 网页控制台(site.register_admin)可视化查看。
