"""并发闸热更新 — Max_Concurrency / 供应商通道闸 / 模型闸 改配置即刻生效"""

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
    monkeypatch.setattr(conc, "_channel_semaphores", {})
    monkeypatch.setattr(conc, "_channel_inflight", {})


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


def test_channel_semaphore_rh_vs_vendors(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(
        cfgmod, "PLUGIN_CONFIG", _FakeCfg(RH_Channel_Concurrency=1, Channel_Concurrency=10)
    )
    rh_sem = conc._get_channel_semaphore("runninghub")
    gpu_sem = conc._get_channel_semaphore("comfyui")
    ark_sem = conc._get_channel_semaphore("ark")
    gw_sem = conc._get_channel_semaphore("gateway")
    fish_sem = conc._get_channel_semaphore("fishaudio")

    assert rh_sem._value == 1 and gpu_sem._value == 1  # RH 相关 = 1
    assert ark_sem._value == 10 and gw_sem._value == 10 and fish_sem._value == 10  # 其他供应商 = 10
    # 各供应商各自一把闸,互不共享(同一模型的 ark/gateway 通道互不挤占)
    assert len({id(rh_sem), id(gpu_sem), id(ark_sem), id(gw_sem), id(fish_sem)}) == 5
    # 同名通道复用同一把闸
    assert conc._get_channel_semaphore("ark") is ark_sem


def test_channel_semaphore_hot_reload_and_default(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg(Channel_Concurrency=10))
    sem1 = conc._get_channel_semaphore("fishaudio")
    assert sem1._value == 10

    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg(Channel_Concurrency=20))
    sem2 = conc._get_channel_semaphore("fishaudio")
    assert sem2 is not sem1 and sem2._value == 20  # 热更新换新闸

    # 缺省/非法配置回落默认:RH=1,其他=10;空通道名归入 "unknown" 一把闸
    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg())
    assert conc._get_channel_semaphore("runninghub")._value == 1
    assert conc._get_channel_semaphore("fishaudio")._value == 10
    assert conc._get_channel_semaphore("") is conc._get_channel_semaphore("unknown")


def test_channel_slot_tracks_inflight_and_capacity(monkeypatch):
    import asyncio

    _reset(monkeypatch)
    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg(RH_Channel_Concurrency=1, Channel_Concurrency=2))

    async def _scenario():
        assert conc.channel_has_capacity("ark")  # 空载 → 有容量

        entered = asyncio.Event()
        release = asyncio.Event()

        async def _hold(name: str):
            async with conc.channel_slot(name):
                entered.set()
                await release.wait()

        t1 = asyncio.create_task(_hold("ark"))
        await entered.wait()
        assert conc._channel_inflight["ark"] == 1
        assert conc.channel_has_capacity("ark")  # 1/2,仍有容量

        entered.clear()
        t2 = asyncio.create_task(_hold("ark"))
        await entered.wait()
        assert conc._channel_inflight["ark"] == 2
        assert not conc.channel_has_capacity("ark")  # 2/2,满载
        assert conc.channel_has_capacity("gateway")  # 其他供应商不受影响

        release.set()
        await asyncio.gather(t1, t2)
        assert conc._channel_inflight["ark"] == 0  # 许可全部释放
        assert conc.channel_has_capacity("ark")

    asyncio.run(_scenario())


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
