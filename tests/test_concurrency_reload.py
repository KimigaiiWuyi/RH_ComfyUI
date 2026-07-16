"""并发闸热更新 — Max_Concurrency / 后端闸 / 模型闸 改配置即刻生效"""

import importlib
from types import SimpleNamespace

conc = importlib.import_module("RH_ComfyUI.core.dispatch.concurrency")
cfgmod = importlib.import_module("RH_ComfyUI.rh_config.comfyui_config")


class _FakeCfg:
    """按 key 返回配置值的假 PLUGIN_CONFIG(缺省键返回 None 触发回落)。"""

    def __init__(self, **values) -> None:
        self._values = values

    def get_config(self, key: str):
        return SimpleNamespace(data=self._values.get(key))


def _reset(monkeypatch):
    monkeypatch.setattr(conc, "_global_semaphore", None)
    monkeypatch.setattr(conc, "_global_limit", 0)
    monkeypatch.setattr(conc, "_backend_semaphores", {})


def _model(name: str, backend: str, max_concurrency: int = 0):
    node = SimpleNamespace(backend=backend)
    return SimpleNamespace(name=name, node=node, max_concurrency=max_concurrency)


def test_global_semaphore_hot_reload(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg(Max_Concurrency=2))
    sem1 = conc._get_global_semaphore()
    assert sem1._value == 2
    assert conc._get_global_semaphore() is sem1  # 配置没变,同一把闸

    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg(Max_Concurrency=5))
    sem2 = conc._get_global_semaphore()
    assert sem2 is not sem1 and sem2._value == 5  # 改配置 → 换新闸,新上限


def test_global_semaphore_sanitizes_bad_value(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg(Max_Concurrency="not-an-int"))
    assert conc._get_global_semaphore()._value == 1  # 非法值回落 1


def test_backend_semaphore_rh_vs_others(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(
        cfgmod, "PLUGIN_CONFIG", _FakeCfg(RH_Backend_Concurrency=1, Backend_Concurrency=10)
    )
    rh_sem = conc._get_backend_semaphore(_model("rh_draw", "rh_app"))
    gpu_sem = conc._get_backend_semaphore(_model("wan", "comfyui"))
    fish_sem = conc._get_backend_semaphore(_model("fishtts", "fishaudio"))
    dance_sem = conc._get_backend_semaphore(_model("seedance2", "seedance"))

    assert rh_sem._value == 1 and gpu_sem._value == 1  # RH 相关 = 1
    assert fish_sem._value == 10 and dance_sem._value == 10  # 其他供应商 = 10
    # 各后端各自一把闸,互不共享
    assert len({id(rh_sem), id(gpu_sem), id(fish_sem), id(dance_sem)}) == 4
    # 同后端不同模型共享同一把闸
    assert conc._get_backend_semaphore(_model("fish_s2", "fishaudio")) is fish_sem


def test_backend_semaphore_hot_reload_and_default(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg(Backend_Concurrency=10))
    sem1 = conc._get_backend_semaphore(_model("fishtts", "fishaudio"))
    assert sem1._value == 10

    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg(Backend_Concurrency=20))
    sem2 = conc._get_backend_semaphore(_model("fishtts", "fishaudio"))
    assert sem2 is not sem1 and sem2._value == 20  # 热更新换新闸

    # 缺省/非法配置回落默认:RH=1,其他=10
    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg())
    assert conc._get_backend_semaphore(_model("rh_draw", "rh_app"))._value == 1
    assert conc._get_backend_semaphore(_model("fishtts", "fishaudio"))._value == 10


def test_backend_of_programmatic_model_falls_back_to_name(monkeypatch):
    model = SimpleNamespace(name="pure_code_model", node=None, max_concurrency=0)
    assert conc._backend_of(model) == "pure_code_model"


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
