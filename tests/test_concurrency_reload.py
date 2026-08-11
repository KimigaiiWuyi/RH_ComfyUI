"""并发闸热更新 — Channel_Concurrency / RH_Channel_Concurrency / (model, channel) 闸

新架构:
  1. 供应商全局闸 key=channel.name,上限 Channel_Concurrency(默认 10)
     例外:runninghub / rh_app / comfyui 归一到共享 key=`rh`,
     上限 RH_Channel_Concurrency(默认 1)——共用同一 RH 账户配额,必须串行化排队
  2. (model, channel) 闸 key=(model.name, channel.name),
     上限 = min(通道基线, model.max_concurrency)
"""

import asyncio
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
    monkeypatch.setattr(conc, "_channel_semaphores", {})
    monkeypatch.setattr(conc, "_channel_inflight", {})
    monkeypatch.setattr(conc, "_pair_semaphores", {})
    monkeypatch.setattr(conc, "_pair_inflight", {})


def test_rh_channels_share_low_concurrency_pool(monkeypatch):
    """RH 相关通道共享一把闸,默认 RH_Channel_Concurrency=1;其它供应商走 Channel_Concurrency"""
    _reset(monkeypatch)
    monkeypatch.setattr(
        cfgmod,
        "PLUGIN_CONFIG",
        _FakeCfg(Channel_Concurrency=10, RH_Channel_Concurrency=1),
    )

    rh_app_sem = conc._get_channel_semaphore("rh_app")
    runninghub_sem = conc._get_channel_semaphore("runninghub")
    comfyui_sem = conc._get_channel_semaphore("comfyui")
    ark_sem = conc._get_channel_semaphore("ark")
    gw_sem = conc._get_channel_semaphore("gateway")
    fish_sem = conc._get_channel_semaphore("fishaudio")

    # RH 三通道共享同一信号量对象,上限 1
    assert rh_app_sem is runninghub_sem is comfyui_sem
    assert rh_app_sem._value == 1
    # 非 RH 各自独立,上限 10
    assert ark_sem._value == 10 and gw_sem._value == 10 and fish_sem._value == 10
    assert len({id(ark_sem), id(gw_sem), id(fish_sem), id(rh_app_sem)}) == 4
    # 同名 channel 复用
    assert conc._get_channel_semaphore("ark") is ark_sem
    assert conc._get_channel_semaphore("rh_app") is rh_app_sem


def test_channel_semaphore_hot_reload_and_default(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg(Channel_Concurrency=10))
    sem1 = conc._get_channel_semaphore("fishaudio")
    assert sem1._value == 10

    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg(Channel_Concurrency=20))
    sem2 = conc._get_channel_semaphore("fishaudio")
    assert sem2 is not sem1 and sem2._value == 20  # 热更新换新闸

    # 缺省/非法配置回落默认 Channel_Concurrency=10;空通道名归入 "unknown"
    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg())
    assert conc._get_channel_semaphore("fishaudio")._value == 10
    assert conc._get_channel_semaphore("") is conc._get_channel_semaphore("unknown")

    # RH 缺省回落 RH_Channel_Concurrency=1
    assert conc._get_channel_semaphore("rh_app")._value == 1


def test_rh_shared_slot_queues_second_request(monkeypatch):
    """RH 共享闸=1 时,第 2 个请求排队等待(不报错),跨 rh_app/runninghub 也串行"""
    _reset(monkeypatch)
    monkeypatch.setattr(
        cfgmod,
        "PLUGIN_CONFIG",
        _FakeCfg(Channel_Concurrency=10, RH_Channel_Concurrency=1),
    )

    async def _scenario():
        entered1 = asyncio.Event()
        release1 = asyncio.Event()
        entered2 = asyncio.Event()

        async def _hold_rh_app():
            async with conc.channel_slot("rh_app"):
                entered1.set()
                await release1.wait()

        async def _try_runninghub():
            async with conc.channel_slot("runninghub"):
                entered2.set()

        t1 = asyncio.create_task(_hold_rh_app())
        await entered1.wait()
        assert conc._channel_inflight[conc._RH_SHARED_KEY] == 1
        assert not conc.channel_has_capacity("rh_app")
        assert not conc.channel_has_capacity("runninghub")  # 共享池,跨通道也满
        assert conc.channel_has_capacity("ark")  # 其它供应商不受影响

        t2 = asyncio.create_task(_try_runninghub())
        # 给若干 tick:t2 应阻塞在信号量上,不能进入
        await asyncio.sleep(0.05)
        assert not entered2.is_set(), "RH 共享闸满时应排队,不应立刻进入"

        release1.set()
        await asyncio.gather(t1, t2)
        assert entered2.is_set(), "前一任务释放后,排队请求应成功进入"
        assert conc._channel_inflight.get(conc._RH_SHARED_KEY, 0) == 0
        assert conc.channel_has_capacity("rh_app")

    asyncio.run(_scenario())


def test_channel_slot_tracks_inflight_and_capacity(monkeypatch):
    """供应商全局闸(channel_slot):2 个并发 task 占用 ark 到满载,计数正确"""
    _reset(monkeypatch)
    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg(Channel_Concurrency=2))

    async def _scenario():
        assert conc.channel_has_capacity("ark")  # 空载

        entered = asyncio.Event()
        release = asyncio.Event()

        async def _hold(name: str):
            async with conc.channel_slot(name):
                entered.set()
                await release.wait()

        t1 = asyncio.create_task(_hold("ark"))
        await entered.wait()
        assert conc._channel_inflight["ark"] == 1
        assert conc.channel_has_capacity("ark")  # 1/2

        entered.clear()
        t2 = asyncio.create_task(_hold("ark"))
        await entered.wait()
        assert conc._channel_inflight["ark"] == 2
        assert not conc.channel_has_capacity("ark")  # 2/2 满载
        assert conc.channel_has_capacity("gateway")  # 其他 channel 不受影响

        release.set()
        await asyncio.gather(t1, t2)
        assert conc._channel_inflight["ark"] == 0
        assert conc.channel_has_capacity("ark")

    asyncio.run(_scenario())


def test_pair_slot_respects_model_max_concurrency(monkeypatch):
    """(model, channel) 闸:上限 = min(通道基线, model.max_concurrency)"""
    _reset(monkeypatch)
    monkeypatch.setattr(
        cfgmod,
        "PLUGIN_CONFIG",
        _FakeCfg(Channel_Concurrency=10, RH_Channel_Concurrency=1),
    )

    # model.max_concurrency=3 → (model, channel) 闸 = min(10, 3) = 3
    model_a = SimpleNamespace(name="seedance10_pro_fast", max_concurrency=3)
    sem_a = conc._get_pair_semaphore(model_a.name, "aifoundation", conc._pair_limit(model_a, "aifoundation"))
    assert sem_a._value == 3

    # model.max_concurrency=1 → (model, channel) 闸 = 1(本地 ComfyUI 工作流)
    model_b = SimpleNamespace(name="IndexTTS2", max_concurrency=1)
    sem_b = conc._get_pair_semaphore(model_b.name, "comfyui", conc._pair_limit(model_b, "comfyui"))
    assert sem_b._value == 1

    # model.max_concurrency=0 在非 RH 通道 → 退化为 Channel_Concurrency
    model_c = SimpleNamespace(name="fish_tts", max_concurrency=0)
    sem_c = conc._get_pair_semaphore(model_c.name, "fishaudio", conc._pair_limit(model_c, "fishaudio"))
    assert sem_c._value == 10

    # model.max_concurrency=0 在 RH 通道 → 退化为 RH_Channel_Concurrency=1
    model_d = SimpleNamespace(name="anima", max_concurrency=0)
    assert conc._pair_limit(model_d, "rh_app") == 1

    # 不同 (model, channel) 组合 → 独立闸
    assert sem_a is not sem_b and sem_b is not sem_c

    # 同一组合再次取 → 复用同一闸
    assert conc._get_pair_semaphore("seedance10_pro_fast", "aifoundation", 3) is sem_a


def test_pair_slot_isolation_per_model_and_channel(monkeypatch):
    """(model_a, ch) 满载不影响 (model_b, ch);(model, ch_a) 满载不影响 (model, ch_b)"""
    _reset(monkeypatch)
    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg(Channel_Concurrency=1))

    async def _scenario():
        # m1+aifoundation 限 1,先占满
        entered_a = asyncio.Event()
        release_a = asyncio.Event()

        async def _hold_pair(model_name, channel_name, entered, release):
            model = SimpleNamespace(name=model_name, max_concurrency=0)
            async with conc.channel_slot_for_model(model, channel_name):
                entered.set()
                await release.wait()

        t_a = asyncio.create_task(_hold_pair("m1", "aifoundation", entered_a, release_a))
        await entered_a.wait()
        assert conc._pair_inflight[("m1", "aifoundation")] == 1

        # 验证:m2+aifoundation 是独立闸,不应被 m1 阻塞
        entered_b = asyncio.Event()

        async def _try_enter():
            async with conc.channel_slot_for_model(SimpleNamespace(name="m2", max_concurrency=0), "aifoundation"):
                entered_b.set()

        t_b = asyncio.create_task(_try_enter())
        # 给一个 tick 让 t_b 跑起来
        await asyncio.sleep(0.01)
        assert entered_b.is_set(), "m2+aifoundation 应能立即拿到许可(独立于 m1+aifoundation)"

        # 同时:m1 在另一非 RH 通道也是独立 pair 闸
        entered_c = asyncio.Event()

        async def _try_enter_other_ch():
            async with conc.channel_slot_for_model(SimpleNamespace(name="m1", max_concurrency=0), "gateway"):
                entered_c.set()

        t_c = asyncio.create_task(_try_enter_other_ch())
        await asyncio.sleep(0.01)
        assert entered_c.is_set(), "m1+gateway 应能立即拿到许可(独立于 m1+aifoundation)"

        # 释放所有
        release_a.set()
        await asyncio.gather(t_a, t_b, t_c)
        assert conc._pair_inflight[("m1", "aifoundation")] == 0

    asyncio.run(_scenario())


def test_pair_slot_tracks_inflight_and_capacity(monkeypatch):
    """(model, channel) 闸的 in-flight 计数与配对隔离"""
    _reset(monkeypatch)
    monkeypatch.setattr(cfgmod, "PLUGIN_CONFIG", _FakeCfg(Channel_Concurrency=10))

    async def _scenario():
        model = SimpleNamespace(name="m1", max_concurrency=2)  # pair 闸 = 2
        ch = "fishaudio"

        # inflight 用 channel_has_capacity 检查不了(pair 维度),改用 in-flight dict
        entered = asyncio.Event()
        release = asyncio.Event()

        async def _hold():
            async with conc.channel_slot_for_model(model, ch):
                entered.set()
                await release.wait()

        t1 = asyncio.create_task(_hold())
        await entered.wait()
        assert conc._pair_inflight[("m1", ch)] == 1

        entered.clear()
        t2 = asyncio.create_task(_hold())
        await entered.wait()
        assert conc._pair_inflight[("m1", ch)] == 2  # 2/2 满载

        # m2+fishaudio 独立于 m1+fishaudio
        assert ("m2", ch) not in conc._pair_inflight

        release.set()
        await asyncio.gather(t1, t2)
        assert conc._pair_inflight[("m1", ch)] == 0

    asyncio.run(_scenario())
