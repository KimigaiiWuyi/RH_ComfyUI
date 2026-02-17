"""init"""

from gsuid_core.sv import Plugins

Plugins(
    name="RH_ComfyUI",
    force_prefix=["rh", "cf", "RH"],
    allow_empty_prefix=False,
)
