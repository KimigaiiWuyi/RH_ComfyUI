"""插件配置入口。

将配置拆分为两组：

- `SERVICE_CONFIG`: 上游服务连接信息（API Key / BaseURL / 服务地址选项）
- `PLUGIN_CONFIG`: 插件自身行为配置（并发上限、用户初始积分、业务积分消耗）

二者分别持久化到独立的 JSON 文件，并通过 [`StringConfig`](../../../gsuid_core/utils/plugins_config/gs_config.py:40)
注册到 Web 控制台，方便分别管理。Web 控制台展示用 [`GsDivider`](../../../gsuid_core/utils/plugins_config/models.py:93)
按服务 / 按用途分组。
"""

from gsuid_core.utils.plugins_config.gs_config import StringConfig

from .plugin_config import PLUGIN_CONFIG_DEFAULT
from .service_config import SERVICE_CONFIG_DEFAULT
from ..utils.resource.RESOURCE_PATH import (
    PLUGIN_CONFIG_PATH,
    SERVICE_CONFIG_PATH,
)

SERVICE_CONFIG = StringConfig(
    "RHComfyUI-Service",
    SERVICE_CONFIG_PATH,
    SERVICE_CONFIG_DEFAULT,
)

PLUGIN_CONFIG = StringConfig(
    "RHComfyUI-Plugin",
    PLUGIN_CONFIG_PATH,
    PLUGIN_CONFIG_DEFAULT,
)
