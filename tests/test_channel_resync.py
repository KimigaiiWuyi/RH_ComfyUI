"""channel_registry 重绑扩展点:钩子登记 / 失败隔离 / set_config 监视 / 差量同步。"""

from RH_ComfyUI.core import (
    channel_registry,
    log_binding_diff,
    bind_config_resync,
    sync_owned_bindings,
    register_resync_hook,
    resync_channel_bindings,
)
from RH_ComfyUI.core.schema.types import NodeOutput
from RH_ComfyUI.core.channels.resync import channel_resync
from RH_ComfyUI.core.channels.channel import ProviderChannel


class _Ch(ProviderChannel):
    def __init__(self, name: str, weight: int = 1) -> None:
        self.name = name
        self.weight = weight

    async def check_available(self) -> bool:
        return True

    async def invoke(self, **kwargs) -> NodeOutput:
        return NodeOutput()


class _FakeConfig:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def set_config(self, key: str, value: object) -> bool:
        self.values[key] = value
        return True


def setup_function() -> None:
    channel_resync.clear()
    channel_registry.clear()


def teardown_function() -> None:
    channel_resync.clear()
    channel_registry.clear()


def test_resync_runs_registered_hooks_in_order() -> None:
    order: list[str] = []
    register_resync_hook("a", lambda: order.append("a"))
    register_resync_hook("b", lambda: order.append("b"))
    ran = resync_channel_bindings()
    assert order == ["a", "b"]
    assert ran == ["a", "b"]


def test_resync_same_name_replaces_hook() -> None:
    hits: list[int] = []
    register_resync_hook("x", lambda: hits.append(1))
    register_resync_hook("x", lambda: hits.append(2))
    resync_channel_bindings()
    assert hits == [2]


def test_resync_isolates_hook_errors() -> None:
    order: list[str] = []

    def boom() -> None:
        order.append("a")
        raise RuntimeError("hook exploded")

    register_resync_hook("a", boom)
    register_resync_hook("b", lambda: order.append("b"))
    ran = resync_channel_bindings()
    assert order == ["a", "b"]
    assert ran == ["b"]


def test_bind_config_resync_only_on_watched_keys() -> None:
    hits: list[str] = []
    register_resync_hook("t", lambda: hits.append("run"))
    cfg = _FakeConfig()
    bind_config_resync(cfg, frozenset({"Slot_Models", "Slot_Enable"}))

    assert cfg.set_config("other", ["x"]) is True
    assert hits == []
    assert cfg.set_config("Slot_Models", ["banana2"]) is True
    assert hits == ["run"]
    assert cfg.set_config("Slot_Enable", True) is True
    assert hits == ["run", "run"]


def test_bind_config_resync_merges_keys_without_double_wrap() -> None:
    hits: list[int] = []
    register_resync_hook("t", lambda: hits.append(1))
    cfg = _FakeConfig()
    bind_config_resync(cfg, frozenset({"A"}))
    bind_config_resync(cfg, frozenset({"B"}))
    cfg.set_config("A", 1)
    cfg.set_config("B", 2)
    assert hits == [1, 1]


def test_resync_reentry_is_noop() -> None:
    hits: list[int] = []

    def nested() -> None:
        hits.append(1)
        assert resync_channel_bindings() == []

    register_resync_hook("n", nested)
    assert resync_channel_bindings() == ["n"]
    assert hits == [1]


def test_sync_owned_bindings_add_remove_change_and_noop() -> None:
    ch_a = _Ch("gw")
    owned, diff = sync_owned_bindings([], [("banana2", ch_a, "NB2")])
    assert diff.has_changes()
    assert diff.added == (("banana2", "gw", "NB2"),)
    assert [b.vendor_model for b in channel_registry.bindings_for("banana2")] == ["NB2"]

    owned2, diff2 = sync_owned_bindings(owned, [("banana2", ch_a, "NB2")])
    assert not diff2.has_changes()
    assert owned2 == owned

    ch_b = _Ch("gw", weight=2)
    _, diff3 = sync_owned_bindings(owned2, [("banana2", ch_b, "NB2")])
    assert diff3.changed == (("banana2", "gw", "NB2", "NB2"),)

    _, diff4 = sync_owned_bindings([("banana2", "gw")], [("seedance2", _Ch("gw"), "sd2")])
    assert [t[:2] for t in diff4.removed] == [("banana2", "gw")]
    assert [t[:2] for t in diff4.added] == [("seedance2", "gw")]
    assert channel_registry.bindings_for("banana2") == []


def test_register_binding_skips_identical(caplog) -> None:
    import logging

    ch = _Ch("aifoundation")
    assert channel_registry.register_binding("banana2", ch, vendor_model="NB2") is True
    caplog.set_level(logging.INFO)
    caplog.clear()
    assert channel_registry.register_binding("banana2", _Ch("aifoundation"), vendor_model="NB2") is False
    assert "追加" not in caplog.text
    assert len(channel_registry.bindings_for("banana2")) == 1


def test_log_binding_diff_silent_when_unchanged(caplog) -> None:
    import logging

    from RH_ComfyUI.core.channels.resync import BindingDiff

    caplog.set_level(logging.INFO)
    log_binding_diff("[x]", BindingDiff((), (), ()), 10)
    assert caplog.text == ""
    log_binding_diff("[x]", BindingDiff((("m", "c", "v"),), (), ()), 1)
    assert "[x]: +m/c (共 1 条)" in caplog.text
