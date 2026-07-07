# core/telemetry — 生成记录统一落库

| 文件 | 内容 |
|---|---|
| `recorder.py` | `record_dispatch()` — 调度器侧统计封装:产物落盘 OUTPUT_PATH + `record_task()` 落库,补 `entry_point` / `channel` 两个维度 |

落库表为 `utils/database/models.py` 的 `RHComfyuiTaskRecord`
(user_id / task_type / model_name / prompt / point_cost / status / entry_point / channel 等)。

## 维护须知

- 所有失败都不允许影响主流程:内部 try/except 吞错并记日志;
- 闭源通道走同一 `record_dispatch`,只写 channel 名,不落任何私有 URL/参数;
- 新增统计维度:先在 `RHComfyuiTaskRecord` 加列(带默认值),再在这里补写入。
