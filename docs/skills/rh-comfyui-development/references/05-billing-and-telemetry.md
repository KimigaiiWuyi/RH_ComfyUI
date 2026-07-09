# 五、计费与统计

## 5.1 dispatch() 的失败语义(排查问题先背这张表)

`core/dispatch/dispatcher.py`,执行顺序与计费后果:

| 阶段 | 异常 | 计费结果 | 统计 |
|---|---|---|---|
| 1. route() | `ModelUnavailableError` | 不扣费 | 不落库 |
| 2. validate() | `ValidationError` | 不扣费(★校验先于扣费) | 不落库 |
| 3. policy.reserve() | `BillingDeniedError` | 不扣费 | 不落库 |
| 4. model.run() 失败 | 原样抛出 | **先落统计(failed)再退款**(幂等) | status=failed |
| 4. model.run() 取消/中断 | `BaseException`(CancelledError / DryRunInterrupt)原样抛出 | 同样退款(2026-07-10 起;此前 BaseException 绕过退款吞积分) | CancelledError→status=cancelled,其余=failed |
| 4. model.run() 成功 | — | commit | status=ok |

注意:失败路径的 `record_dispatch()` 整体 try/except 兜底**永不抛出**
(它排在退款之前,一旦抛出退款会被跳过);"先落统计再退款"的顺序不许动。

排查"扣了积分没出图":查 `RHComfyuiTaskRecord` 该次记录 —— status=failed
且退款标记为真 = 已退,用户看错了;记录缺失 = 有代码绕过了 dispatch(严重,
必须修);status=ok 但用户没收到 = 入口层发送环节问题。

## 5.2 BillingPolicy 三件套(core/billing/)

```
reserve(ctx, cost) -> BillingReservation   # 预扣;不足抛 BillingDeniedError
commit(reservation)                        # 成功后确认
refund(reservation)                        # 失败退款;必须幂等(判 reservation.refunded)
```

| 实现 | 用于 | 行为 |
|---|---|---|
| `PointsBillingPolicy` | 命令 / Agent 入口 | RHBind 积分真实扣减 |
| `ExternalPrepaidPolicy` | canvas HTTP 入口 | 调用方已扣费,只记账不扣费(防双重扣费) |

新入口有独立钱包 → 写新的 Policy 子类并在入口构造 `DispatchContext` 时
注入;**不要在 dispatcher 里加分支**。

## 5.3 统计落库(core/telemetry/recorder.py)

`record_dispatch()` 做两件事(全部失败不影响主流程):
1. 产物落盘 `OUTPUT_PATH`;
2. `record_task()` 写 `RHComfyuiTaskRecord`(`utils/database/models.py`)。

关键列:`user_id / bot_id / task_type / model_name / prompt /
point_cost / status / entry_point(command|agent|canvas)/ channel /
elapsed_ms / error`。

规则:
- 闭源通道走同一 record_dispatch,只落 `channel` 名,**不落私有 URL/参数**;
- 加统计维度:先在 `RHComfyuiTaskRecord` 加列(带默认值,依赖
  gsuid_core 的 exec_list 自动迁移),再在 recorder 补写入;
- 既有列不改名不删(报表与账单依赖)。

## 5.4 并发闸(core/dispatch/concurrency.py)

- 全局 Semaphore 限制同时执行的生成任务总数(配置键控制);
- 模型级:类属性 `max_concurrency > 0` 时叠加模型自己的闸
  (适合本地显卡型 backend);
- 排队发生在扣费之后 —— 这是有意为之(占坑即占预算),排队过长的
  体验问题靠 on_progress 回调向用户播报。
