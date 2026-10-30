# rh_webconsole — Web 控制台页(模型 / 供应商看板)

| 文件 | 内容 |
|---|---|
| `__init__.py` | `register_plugin_page(...)` — 登记页面到 Hub `/plugins`(插件配置界面)的按钮;`page_id="dashboard"` → `/plugin-pages/rh_comfyui/dashboard/` |
| `dashboard.py` | `build_dashboard_snapshot()` — 只读聚合模型 / 通道 / 熔断 / 并发负载,产出看板快照 |
| `api.py` | `PluginAPI(prefix="/api/RH_ComfyUI/dashboard")` — `GET /overview` |

## 数据来源

| 数据 | 来源 | 说明 |
|---|---|---|
| 模型清单 | `core.routing.registry.model_registry` | 单一事实来源;`pipeline_registry` 是它的派生视图 |
| 供应商通道 | `model.channel_bindings()` | **顺序即负载均衡的输入顺序** |
| 熔断 / 连续失败 | `core.routing.balancer.health_snapshot()` | key 为 `模型/通道` |
| 在途 / 并发上限 | `core.dispatch.concurrency.channel_load_snapshot()` | RH 三通道(`runninghub`/`rh_app`/`comfyui`)共享一把闸 |
| 均衡策略 / 阈值 | `PLUGIN_CONFIG`(`Load_Balance_Mode` / `Failure_Threshold`) | 控制台改完即生效 |

## 口径说明

- **供应商可用性是 (模型, 通道) 粒度的**:同一家的启用闸按模型名逐个判定
  (如 ComfyUI 的工作流白名单),所以供应商卡片上的「可用 N / 共 M」才是聚合后的口径。
- 供应商列表按**首次出现顺序**排列 —— 即各模型声明通道时的先后,不是字母序。
- 外部插件注册的模型 / 通道不受控,取值失败时按「不可用」处理并在日志告警,
  不让一个坏插件拖垮整个看板。

## 维护须知

- 本目录只读。页面上不要提供任何写操作;改配置请回 WebConsole 的插件配置页。
- 模型图标:`dashboard.py` 按名称/后端出 `icon` key,字形在 `web/icons.js`
  (品牌图形提取自 Hub thesvg-icons;无品牌按 `mod:<目录分组>` 回落)。
- 图谱双模式:容器宽 < 760 时 `app.js` 走 `renderCompactGraph()` 整宽卡片 +
  通道 chip(不画连线),宽屏保持两列连线图。
- 静态资源在 `RH_ComfyUI/web/`,三语 locale 的 leaf key 必须对齐
  (`zh-CN` / `en-US` / `ja-JP`)。
- 新增图表只改 `web/app.js`;后端字段不够时再动 `dashboard.py`,别在页面里硬编码模型名。
