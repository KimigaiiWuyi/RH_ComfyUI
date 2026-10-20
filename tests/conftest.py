"""测试环境:把 gsuid_core 与插件根加入 sys.path"""

import os
import sys
from pathlib import Path

# Windows 控制台默认 GBK,spawn 出的子进程写 emoji 日志会 UnicodeEncodeError 而死。
# 在父进程里先定好,子进程继承 env 后 stdout 才是 UTF-8。
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_GSCORE_ROOT = _PLUGIN_ROOT.parents[2]

for p in (str(_GSCORE_ROOT), str(_PLUGIN_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)
