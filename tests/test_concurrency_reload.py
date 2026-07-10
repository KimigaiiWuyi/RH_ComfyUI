"""并发闸热更新 — Max_Concurrency 改配置即刻生效,不再冻结在首次使用"""

import importlib
from types import SimpleNamespace

conc = importlib.import_module("RH_ComfyUI.core.dispatch.concurrency")
cfgmod = importlib.import_module("RH_ComfyUI.rh_config.comfyui_config")


class _FakeCfg:
    def __init__(self, value) -> None:
        self._value = value

    def get_config(self, key: str):
        assert key == "Max_Concurrency"
        return SimpleNamespace(data=self._value)


def _reset(monkeypatch):
    monkeypatch.setattr(conc, "_global_semaphore", None)
    monkeypatch.setattr(conc, "_global_limit", 0)


def test_global_semaphore_hot_reload(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg(2))
    sem1 = conc._get_global_semaphore()
    assert sem1._value == 2
    assert conc._get_global_semaphore() is sem1  # 配置没变,同一把闸

    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg(5))
    sem2 = conc._get_global_semaphore()
    assert sem2 is not sem1 and sem2._value == 5  # 改配置 → 换新闸,新上限


def test_global_semaphore_sanitizes_bad_value(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg("not-an-int"))
    assert conc._get_global_semaphore()._value == 1  # 非法值回落 1


def test_model_semaphore_follows_class_attr(monkeypatch):
    monkeypatch.setattr(conc, "_model_semaphores", {})
    model = SimpleNamespace(name="m1", max_concurrency=2)
    sem1 = conc._get_model_semaphore(model)
    assert sem1 is not None and sem1._value == 2
    assert conc._get_model_semaphore(model) is sem1

    model.max_concurrency = 4
    sem2 = conc._get_model_semaphore(model)
    assert sem2 is not sem1 and sem2._value == 4

    model.max_concurrency = 0
    assert conc._get_model_semaphore(model) is None  # 0=不限,清掉缓存
