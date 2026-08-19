# core/routing — 注册表、路由与负载均衡

| 文件 | 内容 |
|---|---|
| `registry.py` | `ModelRegistry`(name → 模型实例)、`@register_model` 装饰器、`load_entry_point_models()`(pip entry points 装载外部模型) |
| `router.py` | `route()` — 五级路由:显式指定 → 输入档案 supports() → 可用性过滤 → AI 推荐钩子 → 优先级+随机 |
| `balancer.py` | `LoadBalancer` — 通用负载均衡 + 熔断(按 (scope, member) 二级 key;自 seedance registry 泛化) |

## 外部模型接入方式

外部包在自己的 `pyproject.toml` 声明 entry point 组
`rh_comfyui.models`,启动时 `load_entry_point_models()` 自动装载,
本仓库零改动、零条件 import。

## 维护须知

- `route()` 的候选过滤调用 `model.check_available()`,该方法必须廉价(只读配置);
- 熔断阈值/冷却读 PLUGIN_CONFIG 通用键,`Seedance_*` 旧键仅迁移期兜底,勿新增依赖。
