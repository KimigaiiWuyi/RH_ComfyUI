# utils/database — 数据库模型

## 模块概述

`utils/database` 包含 RH_ComfyUI 插件的数据库模型定义。当前仅包含积分管理表 `RHBind`，并注册到 GsCore Web 控制台供管理员可视化管理。

## 文件结构

```
database/
└── models.py      # RHBind 积分表 + Web 控制台注册
```

## 核心组件

### [`RHBind`](RH_ComfyUI/utils/database/models.py:13) 积分表

继承自 GsCore 的 `Bind` 基类（包含 `user_id`、`bot_id`、`group_id` 等公共字段），新增 `point` 字段。

```python
class RHBind(Bind, table=True):
    __table_args__ = {"extend_existing": True}
    point: int = Field(default=20, title="积分")
```

#### 数据库操作方法

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| [`create_data(user_id, bot_id, point?)`](RH_ComfyUI/utils/database/models.py:18) | 用户ID, BotID, 可选积分 | `RHBind` | 创建用户数据（默认积分从配置读取） |
| [`add_point(user_id, bot_id, add_point_num)`](RH_ComfyUI/utils/database/models.py:47) | 用户ID, BotID, 增加数量 | `int` (0=成功) | 增加积分 |
| [`get_point(user_id, bot_id)`](RH_ComfyUI/utils/database/models.py:72) | 用户ID, BotID | `int` | 查询当前积分（不存在返回0） |
| [`deduct_point(user_id, bot_id, deduct_point_num)`](RH_ComfyUI/utils/database/models.py:86) | 用户ID, BotID, 扣除数量 | `bool` | 扣除积分（不足返回 False） |

#### 自动创建机制

所有操作方法都包含自动创建逻辑：当用户不存在时，自动调用 `create_data()` 创建记录，默认积分从配置 `Default_Point` 读取。

### Web 控制台注册 [`SsPushAdmin`](RH_ComfyUI/utils/database/models.py:114)

```python
@site.register_admin
class SsPushAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="AI绘图积分管理",
        icon="fa fa-bullhorn",
    )
    model = RHBind
```

注册后，管理员可在 Web 控制台的「AI绘图积分管理」页面查看和编辑所有用户的积分数据。

## 数据流

```
用户命令（增加积分/查询积分）
    │
    ▼
rh_admin/commands.py
    │
    ▼
RHBind.add_point() / get_point() / deduct_point()
    │
    ▼
GsCore Bind 基类（select_data / insert_data / update_data）
    │
    ▼
SQLModel + SQLAlchemy（SQLite 数据库）
```
