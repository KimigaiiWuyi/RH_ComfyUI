"""模块身份统一 — 保证本插件在进程内只有一棵模块树

同一份代码在运行时可能被三种名字 import:

1. ``plugins.RH_ComfyUI.RH_ComfyUI``  — gsuid_core 嵌套加载器的规范名
2. ``RH_ComfyUI.RH_ComfyUI``          — 经外层 re-export 包(外部插件 等
   跨插件 ``import RH_ComfyUI`` 时,外层 __init__ 触发内层导入)
3. ``RH_ComfyUI``                     — 插件外层目录直接在 sys.path 上时
   (pytest / 独立脚本环境)

若不干预,先后两种 import 会各自 exec 出独立的模块树:模型注册表、
Seedance 负载均衡熔断状态、@on_core_start 钩子全部分裂/重复
(历史症状:启动日志里 "初始化完成" 打印两遍;外部插件注册的模型
进了另一棵树,HTTP 清单看不到)。

unify_module_identity(__name__) 由内层 __init__.py 在装载末尾调用:
1. 把当前树已加载的全部子模块,以其余身份前缀登记进 sys.modules
   (加载器的 cached_import 先查 sys.modules,命中即复用、不再重复 exec);
2. 安装 meta-path finder,后续惰性导入无论走哪个前缀,都定向到本树
   的同一模块对象(create_module 直接返回既有模块,是 setuptools
   _distutils_hack 同款手法)。

注意:裸 ``RH_ComfyUI``(无后缀)不接管 —— 运行时它属于外层 re-export
包;仅其子路径(``RH_ComfyUI.utils`` 等)定向到本树。
"""

from __future__ import annotations

import sys
import importlib
import importlib.abc
import importlib.util
from types import ModuleType
from typing import Optional

#: 本插件已知的三种模块身份前缀(长的在前,匹配时取最长前缀)
IDENTITY_PREFIXES = (
    "plugins.RH_ComfyUI.RH_ComfyUI",
    "RH_ComfyUI.RH_ComfyUI",
    "RH_ComfyUI",
)


def _map_to_canonical(fullname: str, canonical: str) -> Optional[str]:
    """把任意身份前缀下的模块名映射到规范树;不归本模块管则返回 None"""
    for prefix in IDENTITY_PREFIXES:
        if fullname == prefix or fullname.startswith(prefix + "."):
            suffix = fullname[len(prefix) :]
            if prefix == "RH_ComfyUI" and not suffix:
                return None  # 裸外层包不接管
            return canonical + suffix
    return None


class _AliasLoader(importlib.abc.Loader):
    """create_module 直接返回规范模块对象,使多个名字共享同一 module"""

    def __init__(self, module: ModuleType) -> None:
        self._module = module

    def create_module(self, spec) -> ModuleType:  # noqa: ANN001
        return self._module

    def exec_module(self, module: ModuleType) -> None:
        pass  # 规范模块早已执行过,勿重复执行


class _IdentityFinder(importlib.abc.MetaPathFinder):
    def __init__(self, canonical: str) -> None:
        self._canonical = canonical

    def find_spec(self, fullname: str, path=None, target=None):  # noqa: ANN001
        canonical_name = _map_to_canonical(fullname, self._canonical)
        if canonical_name is None or canonical_name == fullname:
            return None
        module = sys.modules.get(canonical_name)
        if module is None:
            try:
                module = importlib.import_module(canonical_name)
            except ModuleNotFoundError:
                return None
        spec = importlib.util.spec_from_loader(fullname, _AliasLoader(module))
        if spec is not None and hasattr(module, "__path__"):
            spec.submodule_search_locations = list(module.__path__)
        return spec


_installed = False


def unify_module_identity(canonical: str) -> None:
    """登记既有子模块别名 + 安装惰性别名 finder(幂等)

    Args:
        canonical: 本树的根模块名(内层 __init__ 传 __name__;
                   进程内第一棵被装载的树即成为规范树)
    """
    global _installed
    if _installed:
        return
    _installed = True

    # 1. 已加载子模块按其余身份前缀登记(供加载器 cached_import 直接复用)
    for name in list(sys.modules):
        if name != canonical and not name.startswith(canonical + "."):
            continue
        suffix = name[len(canonical) :]
        module = sys.modules[name]
        for prefix in IDENTITY_PREFIXES:
            alias = prefix + suffix
            if alias != name and not (prefix == "RH_ComfyUI" and not suffix):
                sys.modules.setdefault(alias, module)

    # 2. 惰性导入统一定向到规范树
    sys.meta_path.insert(0, _IdentityFinder(canonical))
