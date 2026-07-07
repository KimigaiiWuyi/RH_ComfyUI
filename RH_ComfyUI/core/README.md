# core — 生成引擎内核

基于 ABC 的可编程生成架构内核,替代旧的纯 YAML Pipeline 声明方式。
本包是**开源/闭源边界**:闭源插件只允许 `from RH_ComfyUI.core import ...`
(顶层 `__init__.py` 的公开接口),不得深入子模块 import。

## 子目录

| 目录 | 职责 |
|---|---|
| `schema/` | 数据契约:请求/结果/端口/ModelCard(零内部依赖,最底层) |
| `base/` | ABC 五件套(AIGCGenerationBase + 四大模态基类)与错误族 |
| `channels/` | 通道抽象(ProviderChannel / ChannelBinding)与异步轮询工具 |
| `routing/` | ModelRegistry、负载均衡器、route() 路由入口 |
| `billing/` | 计费策略(积分 / 外部预付)与预留-提交-退款协议 |
| `dispatch/` | 统一调度器:路由 → 计费 → 限流 → 执行 → 统计 → 退款 |
| `telemetry/` | 生成记录统一落库(record_dispatch) |

## 依赖方向(只允许向下)

```
dispatch → routing / billing / telemetry → base → channels → schema
```

## 维护须知

- 新增公开符号必须同时加进本目录 `__init__.py` 的 re-export 与 `__all__`;
- 不要在 core 内 import `utils/`、`models/`、任何入口包 —— 内核不感知上层;
- 单元测试位于插件根 `tests/`,全部离线(FakeModel / FakePolicy),改动内核先跑
  `python -m pytest tests/ -q`。
