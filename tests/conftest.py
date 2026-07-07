"""测试环境:把 gsuid_core 与插件根加入 sys.path"""

import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_GSCORE_ROOT = _PLUGIN_ROOT.parents[2]

for p in (str(_GSCORE_ROOT), str(_PLUGIN_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)
