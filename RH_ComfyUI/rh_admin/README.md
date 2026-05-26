# rh_admin — 积分管理模块

## 模块概述

`rh_admin` 是 RH_ComfyUI 插件的**积分管理子模块**，负责用户的积分增减和查询操作。该模块同时支持命令行触发和 AI Tools 两种调用方式，是插件经济系统的核心。

## 文件结构

```
rh_admin/
├── __init__.py      # 命令处理器（SV 触发器注册）
└── commands.py      # 核心逻辑函数 + AI Tools 注册
```

## 核心组件

### 1. 命令处理器 [`__init__.py`](RH_ComfyUI/rh_admin/__init__.py:1)

注册了两个 SV 实例和三个命令触发器：

| SV 实例 | 权限等级 | 说明 |
|---------|---------|------|
| `sv_admin` | `pm=0` | 仅限主人/管理员操作 |
| `sv_user` | `pm=6` | 所有用户可用 |

**注册的命令：**

| 命令关键词 | 处理函数 | 权限 | 说明 |
|-----------|---------|------|------|
| `增加积分` / `加积分` | [`add_points()`](RH_ComfyUI/rh_admin/__init__.py:29) | 管理员 | 为指定用户增加积分 |
| `减少积分` / `扣积分` | [`deduct_points()`](RH_ComfyUI/rh_admin/__init__.py:54) | 管理员 | 扣除指定用户积分 |
| `查询积分` / `查看积分` | [`query_points()`](RH_ComfyUI/rh_admin/__init__.py:79) | 普通用户 | 查询积分（普通用户仅查自己） |

**命令格式：**
```
增加积分 <@用户> <积分数量>
减少积分 <@用户> <积分数量>
查询积分          # 查询自己的
查询积分 <@用户>  # 管理员查询他人
```

### 2. 核心逻辑 [`commands.py`](RH_ComfyUI/rh_admin/commands.py:1)

#### 参数解析函数

- [`parse_add_points_args(ev)`](RH_ComfyUI/rh_admin/commands.py:22) — 解析增加/减少积分命令的参数，支持 `@用户` 和 `用户ID` 两种格式
- [`parse_query_points_args(ev)`](RH_ComfyUI/rh_admin/commands.py:60) — 解析查询积分命令的参数，自动处理权限校验

#### AI Tools 注册

所有核心函数都通过 `@ai_tools` 装饰器注册为 AI 工具，AI Agent 可直接调用：

| 函数 | 装饰器 | check_func | 说明 |
|------|--------|------------|------|
| [`add_user_points()`](RH_ComfyUI/rh_admin/commands.py:107) | `@ai_tools(check_func=check_pm)` | `check_pm` | 增加积分（需管理员权限） |
| [`deduct_user_points()`](RH_ComfyUI/rh_admin/commands.py:141) | `@ai_tools(check_func=check_pm)` | `check_pm` | 扣除积分（需管理员权限） |
| [`query_user_points()`](RH_ComfyUI/rh_admin/commands.py:184) | `@ai_tools` | 无 | 查询积分（所有用户可用） |

#### 权限校验 [`check_pm()`](RH_ComfyUI/rh_admin/commands.py:92)

```python
def check_pm(ev: Event) -> Tuple[bool, str]:
    if ev.user_pm == 0:
        return True, "✅ 您是管理员，为你进行操作！"
    return False, "🚫 您不是管理员，无法执行此操作！"
```

使用 `Annotated[str, Meta(description="...")]` 为 AI Tools 提供参数描述，让 AI 能正确构建调用参数。

## 数据依赖

- 依赖 [`RHBind`](RH_ComfyUI/utils/database/models.py:13) 数据模型进行积分的增删改查
- 通过 `RHBind.add_point()` / `RHBind.deduct_point()` / `RHBind.get_point()` 操作数据库

## 与其他模块的关系

```
rh_admin ──→ utils/database/models.py (RHBind)
         ──→ AI Agent（通过 @ai_tools 注册为可调用工具）
```
