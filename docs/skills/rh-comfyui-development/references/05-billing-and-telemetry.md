# 五、计费与统计

## 5.1 dispatch() 的失败语义(排查问题先背这张表)

`core/dispatch/dispatcher.py`,执行顺序与计费后果:

| 阶段 | 异常 | 计费结果 | 统计 |
|---|---|---|---|
| 1. route() | `ModelUnavailableError` | 不扣费 | 不落库 |
| 2. validate() | `ValidationError` | 不扣费(★校验先于扣费) | 不落库 |
| 3. policy.reserve(estimate_cost) | `BillingDeniedError` | 不扣费 | 不落库 |
| 4. model.run() 失败 | 原样抛出 | **先落统计(failed)再退款**(幂等) | status=failed |
| 4. model.run() 超时 | 超过 `Dispatch_Timeout` → `GenerationError`("生成超时") | 退款 | status=failed(不是 cancelled) |
| 4. model.run() 取消/中断 | `BaseException`(CancelledError / DryRunInterrupt)原样抛出 | 同样退款(2026-07-10 起;此前 BaseException 绕过退款吞积分) | CancelledError→status=cancelled,其余=failed |
| 4. model.run() 成功 | — | commit | status=ok |

**预扣金额 = `model.estimate_cost(request)`**(动态计费钩子,默认 = 静态
`point_cost`):校验通过后调用一次,该值同时用于 reserve / `result.cost_points`
/ 统计表 `point_cost` 落库,三处口径一致。按参数分档计费的模型只需覆盖
estimate_cost,dispatcher 与统计不用改。

**超时预算**:`PLUGIN_CONFIG.Dispatch_Timeout`(秒,默认 1800,0=不限,
每次 dispatch 实时读、改配置即刻生效)用 `asyncio.wait_for` 包住
"排队等并发闸 + model.run()" 全程 —— 排队发生在扣费之后,没有预算时一个
卡死的上游会占住全局并发闸拖垮后续所有任务。超时被翻译成带人话
user_message 的 `GenerationError`,按普通失败退款落库。

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

- 全局 Semaphore 限制同时执行的生成任务总数(`PLUGIN_CONFIG.Max_Concurrency`);
- **上限热更新**:每次取闸都重读配置,数值变了就换一把新信号量 —— 新任务立刻
  按新上限排队;已在执行的任务持有旧许可自然完成(收缩上限时总并发随旧任务
  完成逐渐收敛,不中断在跑任务)。模型级闸同理跟随 `max_concurrency` 类属性;
- 模型级:类属性 `max_concurrency > 0` 时叠加模型自己的闸
  (适合本地显卡型 backend);
- 排队发生在扣费之后 —— 这是有意为之(占坑即占预算),排队过长的
  体验问题靠 on_progress 回调向用户播报;总时长兜底由 §5.1 的
  `Dispatch_Timeout` 超时预算负责。

## 5.5 供应商对账

统计表已带 `backend_provider / backend_key_prefix / elapsed_ms / status` 维度,
聚合出口是 `RHComfyuiTaskRecord.get_provider_summaries()`(按供应商聚合
总单数/成功率/平均耗时/总积分,空 provider 的单通道历史记录不计入)+
管理员命令 `rh 供应商统计 [最近N天]`(附本次运行期熔断快照;见 §10、§13.6)。
