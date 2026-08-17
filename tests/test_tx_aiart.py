"""腾讯云混元扩图:TC3 签名确定性 + Adapter 注册。"""

from __future__ import annotations

from RH_ComfyUI.utils.backends.tx_aiart.api import sign_tc3


def test_sign_tc3_deterministic():
    payload = '{"Ratio":"16:9","LogoAdd":0}'
    kwargs = dict(
        secret_id="AKIDtest",
        secret_key="secret",
        service="aiart",
        host="aiart.tencentcloudapi.com",
        action="ImageOutpainting",
        payload=payload,
        timestamp=1700000000,
        region="ap-guangzhou",
        version="2022-12-29",
    )
    a = sign_tc3(**kwargs)
    b = sign_tc3(**kwargs)
    assert a["Authorization"] == b["Authorization"]
    assert a["Authorization"].startswith("TC3-HMAC-SHA256 Credential=AKIDtest/")
    assert "SignedHeaders=content-type;host;x-tc-action" in a["Authorization"]
    assert a["X-TC-Action"] == "ImageOutpainting"
    assert a["X-TC-Timestamp"] == "1700000000"
    assert a["Host"] == "aiart.tencentcloudapi.com"


def test_sign_tc3_changes_with_secret():
    payload = '{"Ratio":"16:9"}'
    common = dict(
        secret_id="AKIDtest",
        service="aiart",
        host="aiart.tencentcloudapi.com",
        action="ImageOutpainting",
        payload=payload,
        timestamp=1700000000,
        region="ap-guangzhou",
        version="2022-12-29",
    )
    a = sign_tc3(secret_key="aaa", **common)
    b = sign_tc3(secret_key="bbb", **common)
    assert a["Authorization"] != b["Authorization"]


def test_tx_aiart_adapter_registered():
    from RH_ComfyUI.utils.backends import backend_registry, init_backends

    init_backends()
    adapter = backend_registry.get("tx_aiart")
    assert adapter is not None
    assert adapter.name == "tx_aiart"


def test_tx_image_outpaint_in_all_models():
    from RH_ComfyUI.models.image.defs import ALL_MODELS, TxImageOutpaintDef

    assert TxImageOutpaintDef in ALL_MODELS
    node = TxImageOutpaintDef.node_def()
    assert node.name == "tx_image_outpaint"
    assert node.backend == "tx_aiart"
    assert node.catalog_group == "tool"
    assert node.point_cost == 2
