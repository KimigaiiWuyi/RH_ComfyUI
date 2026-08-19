# core/dispatch — 统一调度器

三大入口(命令 / AI Agent / HTTP)全部经由 `dispatch()` 执行,
这是唯一执行路径,也是计费/统计的强制拦截点。

| 文件 | 内容 |
|---|---|
| `dispatcher.py` | `dispatch(request, ctx)`:route → validate(不扣费)→ reserve → 并发闸 + 执行 → settle/refund → record_dispatch |
| `context.py` | `DispatchContext` — 入口注入的执行环境(BillingContext + BillingPolicy + 进度回调) |
| `concurrency.py` | `generation_slot()` — 全局 Semaphore 并发闸(平移自 utils/core/executor)+ 可选模型级闸 |

## 失败语义

| 阶段 | 异常 | 计费结果 |
|---|---|---|
| 路由 | `ModelUnavailableError` | 不扣费 |
| 校验 | `ValidationError` | 不扣费(校验先于扣费) |
| 预留 | `BillingDeniedError` | 不扣费 |
| 执行失败 | 原样抛出 | 已预留额度全额退款(幂等) |
| 成功 | — | settle(预扣后按 usage 差额对齐,禁止双重扣费) + 落库 |

## 维护须知

- 不要绕过 `dispatch()` 直接调 `model.run()`(统计与计费会丢);
- 统计落库失败不影响主流程(recorder 内部吞错并记日志)。
