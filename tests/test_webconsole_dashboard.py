"""Web 控制台看板快照 — 模型状态 / 供应商状态 / 供应商顺序 / 继承模型可用性

看板页面(`RH_ComfyUI/web/`)本身是只读渲染,契约全在后端快照
(`rh_webconsole.dashboard.build_dashboard_snapshot`)上。这里冻结四件事:

1. 模型状态:模型级可用性 + 不可用原因;
2. 供应商状态:按供应商聚合的可用模型数、熔断模型、在途/上限;
3. 供应商顺序:模型通道的**声明顺序**即负载均衡的输入顺序;
4. 继承模型可用性:一家供应商被哪些模型继承、各自是否可用。
"""

from __future__ import annotations

import json
import asyncio
from typing import Any, Optional
from pathlib import Path

import pytest

from RH_ComfyUI.core import (
    PortSpec,
    PortType,
    TaskType,
    ModelCard,
    NodeOutput,
    ChannelBinding,
    ProviderChannel,
    AIGCGenerationBase,
)
from RH_ComfyUI.core.routing.balancer import LoadBalancer
from RH_ComfyUI.core.routing.registry import ModelRegistry
from RH_ComfyUI.rh_webconsole.dashboard import channel_label, build_dashboard_snapshot

WEB_DIR = Path(__file__).resolve().parent.parent / "RH_ComfyUI" / "web"


class _FakeChannel(ProviderChannel):
    """固定可用性的假通道(零网络)。"""

    def __init__(self, name: str, *, available: bool = True, weight: int = 1) -> None:
        self.name = name
        self.weight = weight
        self._available = available

    async def check_available(self) -> bool:
        return self._available

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        return NodeOutput()


class _FakeModel(AIGCGenerationBase):
    """带固定通道列表的假模型。"""

    name = "fake_model"
    display_name = "Fake Model"
    modality: TaskType = TaskType.IMAGE
    card = ModelCard(description="测试模型")
    point_cost = 3
    priority = 50

    def __init__(self, name: str, channels: list[ProviderChannel], *, priority: int = 50) -> None:
        self.name = name
        self.display_name = name.upper()
        self._channels = channels
        self.priority = priority

    def input_schema(self) -> dict[str, PortSpec]:
        return {"prompt": PortSpec(type=PortType.TEXT, required=True, description="描述")}

    def channel_bindings(self) -> list[ChannelBinding]:
        return [ChannelBinding(channel=c, vendor_model=f"{c.name}-vm") for c in self._channels]

    async def execute_on_channel(
        self,
        request: Any,
        binding: ChannelBinding,
        *,
        on_progress: Optional[Any] = None,
    ) -> NodeOutput:
        return await binding.channel.invoke(request=request)


class _FakeVideoModel(_FakeModel):
    modality: TaskType = TaskType.VIDEO


def _build_world() -> tuple[_FakeModel, _FakeModel, _FakeModel]:
    """三个模型:m1 全通;m2 靠 beta 兜住(gamma 不可用);m3 唯一通道不可用。"""
    alpha = _FakeChannel("alpha", weight=3)
    beta_ok = _FakeChannel("beta")
    beta_bad = _FakeChannel("gamma", available=False)

    m1 = _FakeModel("m1", [alpha, beta_ok])
    m2 = _FakeVideoModel("m2", [beta_ok, beta_bad])
    m3 = _FakeModel("m3", [beta_bad])
    return m1, m2, m3


def _isolate(monkeypatch: pytest.MonkeyPatch, models: list[AIGCGenerationBase]) -> None:
    """用独立注册表替换全局注册表。

    不能直接往全局 ``model_registry`` 注册再 clear():那会连带清掉别的用例
    已经装载的内置模型,导致后续用例莫名其妙失败。
    """
    registry = ModelRegistry()
    for model in models:
        registry.register(model)
    monkeypatch.setattr("RH_ComfyUI.core.routing.registry.model_registry", registry)


def test_snapshot_reports_model_channel_order_and_provider_inheritance(monkeypatch):
    """供应商顺序 = 声明顺序;供应商反向聚合出继承的模型。"""
    monkeypatch.setattr(
        "RH_ComfyUI.core.routing.balancer.get_default_balancer",
        lambda: LoadBalancer(),
    )
    m1, m2, m3 = _build_world()
    _isolate(monkeypatch, [m1, m2, m3])
    snapshot = asyncio.run(build_dashboard_snapshot())

    models = {m["name"]: m for m in snapshot["models"]}
    assert set(models) == {"m1", "m2", "m3"}

    # 供应商顺序:position 必须等于声明下标
    m1_channels = models["m1"]["channels"]
    assert [c["name"] for c in m1_channels] == ["alpha", "beta"]
    assert [c["position"] for c in m1_channels] == [0, 1]
    assert m1_channels[0]["vendor_model"] == "alpha-vm"
    assert m1_channels[0]["weight"] == 3

    # 供应商列表按首次出现顺序(alpha → beta → gamma),不是字母序
    assert [p["name"] for p in snapshot["providers"]] == ["alpha", "beta", "gamma"]

    # 继承关系:beta 被两个模型继承,alpha 只被 m1 继承
    providers = {p["name"]: p for p in snapshot["providers"]}
    assert providers["alpha"]["models"] == ["m1"]
    assert providers["beta"]["models"] == ["m1", "m2"]
    assert providers["beta"]["model_count"] == 2
    assert providers["beta"]["available_model_count"] == 2
    assert providers["beta"]["fully_available"] is True
    # gamma 被 m2 / m3 继承,但两路都不可用
    assert providers["gamma"]["models"] == ["m2", "m3"]
    assert providers["gamma"]["model_count"] == 2
    assert providers["gamma"]["available_model_count"] == 0
    assert providers["gamma"]["unavailable_model_count"] == 2
    assert providers["gamma"]["fully_available"] is False

    # 模型级状态:模型可用 = 任一通道可用
    assert models["m1"]["available"] is True
    assert models["m1"]["unavailable_reason"] == ""
    assert models["m2"]["available"] is True  # beta 兜住
    assert models["m2"]["channels"][1]["available"] is False
    assert models["m2"]["channels"][1]["name"] == "gamma"
    assert models["m3"]["available"] is False  # 唯一通道不可用
    assert models["m3"]["unavailable_reason"]

    # 模态统计按目录分组
    modalities = {m["key"]: m for m in snapshot["modalities"]}
    assert modalities["image"]["total"] == 2
    assert modalities["image"]["available"] == 1
    assert modalities["video"]["total"] == 1
    assert modalities["video"]["available"] == 1

    totals = snapshot["totals"]
    assert totals["models"] == 3
    assert totals["models_available"] == 2
    assert totals["providers"] == 3
    assert totals["providers_healthy"] == 2
    assert totals["links"] == 5
    assert totals["links_available"] == 3


def test_snapshot_surfaces_circuit_breaker_and_concurrency(monkeypatch):
    """熔断状态按 (模型, 通道) 落到通道与供应商两层。"""
    balancer = LoadBalancer(config=None)
    for _ in range(3):
        balancer.record_failure(scope="m1", member="alpha")
    monkeypatch.setattr(
        "RH_ComfyUI.core.routing.balancer.get_default_balancer",
        lambda: balancer,
    )
    m1, m2, m3 = _build_world()
    _isolate(monkeypatch, [m1, m2, m3])
    snapshot = asyncio.run(build_dashboard_snapshot())

    models = {m["name"]: m for m in snapshot["models"]}
    alpha_channel = models["m1"]["channels"][0]
    assert alpha_channel["name"] == "alpha"
    assert alpha_channel["failure_count"] == 3
    assert alpha_channel["circuit_open"] is True
    # 熔断是 (模型, 通道) 粒度:别的模型挂同一通道不受影响
    assert models["m2"]["channels"][0]["circuit_open"] is False

    providers = {p["name"]: p for p in snapshot["providers"]}
    assert providers["alpha"]["circuit_open_models"] == ["m1"]
    assert providers["alpha"]["failure_total"] == 3
    assert snapshot["totals"]["circuit_open"] == 1

    # 并发闸快照:在途 0,上限来自 PLUGIN_CONFIG 基线
    assert alpha_channel["inflight"] == 0
    assert alpha_channel["concurrency_limit"] > 0


def test_snapshot_survives_broken_external_model(monkeypatch):
    """外部插件模型取值抛错时按不可用处理,不拖垮整个看板。"""

    class _BrokenModel(_FakeModel):
        def channel_bindings(self) -> list[ChannelBinding]:
            raise RuntimeError("外部插件炸了")

        async def check_available(self) -> bool:
            raise RuntimeError("外部插件炸了")

    monkeypatch.setattr(
        "RH_ComfyUI.core.routing.balancer.get_default_balancer",
        lambda: LoadBalancer(),
    )
    _isolate(monkeypatch, [_BrokenModel("broken", [])])
    snapshot = asyncio.run(build_dashboard_snapshot())

    models = {m["name"]: m for m in snapshot["models"]}
    assert models["broken"]["available"] is False
    assert models["broken"]["channels"] == []
    assert snapshot["totals"]["models"] == 1


def test_channel_label_falls_back_to_raw_name():
    assert channel_label("ark") == "火山方舟 ARK"
    assert channel_label("gateway_slot1_seedance") == "gateway_slot1_seedance"


# ════════════════════ 静态页与多语言 ════════════════════


def _flatten(data: dict[str, Any], prefix: str = "") -> set[str]:
    out: set[str] = set()
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out |= _flatten(value, path)
        else:
            out.add(path)
    return out


def test_locales_leaf_keys_aligned():
    """三语 leaf key 必须对齐(SDK 用点路径取值,缺一个就露原文)。"""
    catalogs = {}
    for locale in ("zh-CN", "en-US", "ja-JP"):
        path = WEB_DIR / "locales" / f"{locale}.json"
        assert path.is_file(), f"缺少语言包: {path}"
        catalogs[locale] = json.loads(path.read_text(encoding="utf-8"))

    base = _flatten(catalogs["zh-CN"])
    for locale in ("en-US", "ja-JP"):
        keys = _flatten(catalogs[locale])
        assert keys == base, f"{locale} 的 leaf key 与 zh-CN 不一致: 多 {keys - base},缺 {base - keys}"


def test_web_static_assets_present():
    for name in ("index.html", "app.js", "app.css"):
        assert (WEB_DIR / name).is_file(), f"缺少静态资源: {name}"
    index = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert "/plugin-pages/_sdk/gshub-plugin.js" in index
    assert "./app.js" in index
    assert "./app.css" in index


def test_plugin_page_registered_for_hub():
    """页面必须登记到 Hub 的插件配置界面(/plugins 的按钮)。"""
    import RH_ComfyUI.rh_webconsole  # noqa: F401
    from gsuid_core.webconsole.plugin_page import get_plugin_page, pages_for_plugin

    spec = get_plugin_page("rh_comfyui", "dashboard")
    assert spec is not None
    assert spec.plugin_id == "rh_comfyui"
    assert spec.page_id == "dashboard"
    assert spec.index == "index.html"
    assert spec.static_dir == WEB_DIR

    pages = pages_for_plugin("RH_ComfyUI")
    assert any(p["id"] == "dashboard" for p in pages)
