from gsuid_core.utils.plugins_config.gs_config import StringConfig

from .config_default import CONFIG_DEFAULT
from ..utils.resource.RESOURCE_PATH import CONFIG_PATH

RHCOMFYUI_CONFIG = StringConfig(
    "RHComfyUI",
    CONFIG_PATH,
    CONFIG_DEFAULT,
)
