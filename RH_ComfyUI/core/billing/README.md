# core/billing — 计费策略(预留-提交-退款协议)

| 文件 | 内容 |
|---|---|
| `policy.py` | `BillingPolicy` ABC(`reserve` / `settle` / `commit` / `refund`)、`BillingContext`、`BillingReservation` |
| `points_policy.py` | `PointsBillingPolicy` — RHBind 预扣 + 成功后按 `settle` 差额对齐 |
| `external_policy.py` | `ExternalPrepaidPolicy` — 调用方已扣费(HTTP 入口):引擎只记账实扣,不操作钱包 |
| `settle.py` | `settle_cost` 调用壳 / `settle_model_cost`(宿主 resume 用) |

## 语义保证(由 dispatcher 保证,策略实现须配合)

- 校验先于扣费:`ValidationError` 时 `reserve()` 不会被调用;
- 失败必退款:`refund()` 必须幂等(`reservation.refunded` 判重);
- 成功后结算:`policy.settle(reservation, model.settle_cost(request, usage))`
  **只做与预扣的差额**(多退少补)。`actual is None` 则预扣即终扣。
  **禁止**在已预扣后再按 actual 全额扣一次;
- HTTP/ExternalPrepaid:引擎不碰 RHBind,把 `result.cost_points` 写成实扣,
  调用方对已预扣账本做同样的差额对齐;
- 历史任务按供应商原始 `usage` 回算:见 `reconcile.py` /
  `reconcile_seedance_usage_billing`(只处理有原始响应的成功单,差额对齐,禁止双重扣费)。
- 无论哪个策略,消费金额都会写入统计表(`point_cost` 列),外部通道同样走此协议。

## 维护须知

新入口若有独立的钱包体系,新增一个 Policy 子类即可,不要在 dispatcher 里加分支。
