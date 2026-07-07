# core/billing — 计费策略(预留-提交-退款协议)

| 文件 | 内容 |
|---|---|
| `policy.py` | `BillingPolicy` ABC(`reserve` / `commit` / `refund`)、`BillingContext`(user_id/bot_id/entry_point)、`BillingReservation` |
| `points_policy.py` | `PointsBillingPolicy` — RHBind 积分扣费(命令/Agent 入口用) |
| `external_policy.py` | `ExternalPrepaidPolicy` — 调用方已扣费(canvas HTTP 入口):只记账不扣费,避免双重扣费 |

## 语义保证(由 dispatcher 保证,策略实现须配合)

- 校验先于扣费:`ValidationError` 时 `reserve()` 不会被调用;
- 失败必退款:`refund()` 必须幂等(`reservation.refunded` 判重);
- 无论哪个策略,消费金额都会写入统计表(`point_cost` 列),闭源通道同样走此协议。

## 维护须知

新入口若有独立的钱包体系,新增一个 Policy 子类即可,不要在 dispatcher 里加分支。
