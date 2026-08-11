"""schema_validator + Seedance/Wan 跨字段约束(需加载真实 YAML)"""

import pytest

from RH_ComfyUI.core.base.errors import ValidationError
from RH_ComfyUI.core.schema.types import PortSpec, PortType, audio_ref, video_ref
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.core.base.schema_validator import (
    schema_supports_request,
    validate_against_schema,
)


def _req(**kw) -> GenerationRequest:
    kw.setdefault("task_type", TaskType.IMAGE)
    return GenerationRequest(**kw)


def test_required_missing():
    schema = {"prompt": PortSpec(type=PortType.TEXT, required=True)}
    with pytest.raises(ValidationError):
        validate_against_schema(_req(prompt=""), schema, model_name="m")


def test_enum_and_range():
    schema = {
        "resolution": PortSpec(type=PortType.ENUM, values=["480p", "720p"]),
        "duration": PortSpec(type=PortType.INTEGER, minimum=2, maximum=8),
    }
    validate_against_schema(_req(prompt="x", resolution="720p", duration=5), schema, model_name="m")
    with pytest.raises(ValidationError):
        validate_against_schema(_req(prompt="x", resolution="4k"), schema, model_name="m")
    with pytest.raises(ValidationError):
        validate_against_schema(_req(prompt="x", duration=99), schema, model_name="m")


def test_list_cardinality():
    schema = {"images": PortSpec(type=PortType.LIST, item_type=PortType.IMAGE, max_items=2)}
    with pytest.raises(ValidationError):
        validate_against_schema(_req(prompt="x", images=[b"1", b"2", b"3"]), schema, model_name="m")


def test_params_bypass():
    schema = {"service_tier2": PortSpec(type=PortType.ENUM, values=["default", "flex"])}
    validate_against_schema(_req(prompt="x", params={"service_tier2": "flex"}), schema, model_name="m")
    with pytest.raises(ValidationError):
        validate_against_schema(_req(prompt="x", params={"service_tier2": "bad"}), schema, model_name="m")


def test_supports_request_profiles():
    t2i = {"prompt": PortSpec(type=PortType.TEXT, required=True)}
    edit = {"images": PortSpec(type=PortType.LIST, item_type=PortType.IMAGE, min_items=1, max_items=3)}
    assert schema_supports_request(_req(prompt="x"), t2i) is True
    assert schema_supports_request(_req(prompt="x", images=[b"1"]), t2i) is False
    assert schema_supports_request(_req(prompt="x"), edit) is False
    assert schema_supports_request(_req(prompt="x", images=[b"1"]), edit) is True
    assert schema_supports_request(_req(prompt="x", audio_refs=[audio_ref(data=b"ID3")]), t2i) is False


# ── Wan 2.2 input schema(2026-07 收紧:仅支持首帧 + 尾帧,最多 2 张) ──


def test_wan22_schema_max_items_2():
    """Wan 2.2 仅支持首尾帧:images 端口 max_items 必须为 2,不能再伪装支持 9 张"""
    wan22_schema = {
        "prompt": PortSpec(type=PortType.TEXT, required=True),
        "images": PortSpec(
            type=PortType.LIST,
            item_type=PortType.IMAGE,
            min_items=0,
            max_items=2,
            description="Wan 2.2 仅支持首尾帧,最多 2 张图",
        ),
    }
    # 0/1/2 张图都通过输入档案校验
    assert schema_supports_request(_req(task_type=TaskType.VIDEO, prompt="x"), wan22_schema) is True
    assert schema_supports_request(_req(task_type=TaskType.VIDEO, prompt="x", images=[b"1"]), wan22_schema) is True
    assert (
        schema_supports_request(_req(task_type=TaskType.VIDEO, prompt="x", images=[b"1", b"2"]), wan22_schema) is True
    )
    # 3+ 张图直接被路由层拦下(不需要走 validate 才知道)
    assert (
        schema_supports_request(
            _req(task_type=TaskType.VIDEO, prompt="x", images=[b"1", b"2", b"3"]),
            wan22_schema,
        )
        is False
    )
    assert (
        schema_supports_request(
            _req(
                task_type=TaskType.VIDEO,
                prompt="x",
                images=[b"1", b"2", b"3", b"4", b"5"],
            ),
            wan22_schema,
        )
        is False
    )


def test_wan22_validate_rejects_over_limit():
    """Wan22VideoModel.validate() 兜底:即便 schema_supports_request 被绕过,
    也应对 >2 张图给出明确错误并指向 Seedance 替代品。"""
    from RH_ComfyUI.models.video import Wan22VideoModel
    from RH_ComfyUI.models.video.defs import Wan22VideogenDef
    from RH_ComfyUI.core.schema.request import TaskType as LegacyTaskType

    # 拿真实的 Wan22VideogenDef 节点定义,构造一个桥接模型实例
    node = Wan22VideogenDef.node_def()
    model = Wan22VideoModel(node)

    # 3 张图 → 应抛 ValidationError(指向 Seedance 2.0)
    req = GenerationRequest(
        task_type=LegacyTaskType.VIDEO,
        prompt="x",
        images=[b"1", b"2", b"3"],
    )
    with pytest.raises(ValidationError) as ei:
        model.validate(req)
    msg = str(ei.value)
    assert "Wan 2.2" in msg
    assert "Seedance" in msg  # 必须给出替代方案

    # 0/1/2 张图应通过校验(后续容许 validate 抛其它错 —— 不在本测试关注范围)
    for n in (0, 1, 2):
        req = GenerationRequest(
            task_type=LegacyTaskType.VIDEO,
            prompt="x",
            images=[b"i"] * n,
        )
        try:
            model.validate(req)
        except ValidationError as e:
            # 仅允许非"图片数量"的错误(如像素积超限)出现;本测试用的 width/height
            # 默认值不会触发像素积问题,所以此处不期望抛错。
            assert "首帧" not in str(e) and "尾帧" not in str(e)


def test_wan22_validate_rejects_video_audio_refs():
    """Wan 2.2 仍然不支持 video_refs / audio_refs(独立于图片数量收紧)"""
    from RH_ComfyUI.models.video import Wan22VideoModel
    from RH_ComfyUI.models.video.defs import Wan22VideogenDef
    from RH_ComfyUI.core.schema.request import TaskType as LegacyTaskType

    model = Wan22VideoModel(Wan22VideogenDef.node_def())
    # 视频参考至少要一个 MediaRef:用最小的 mp4 magic bytes 触发 MediaRef mime 嗅探
    minimal_mp4 = b"\x00\x00\x00\x14ftypisom" + b"\x00" * 16
    req = GenerationRequest(
        task_type=LegacyTaskType.VIDEO,
        prompt="x",
        video_refs=[video_ref(data=minimal_mp4)],
    )
    with pytest.raises(ValidationError) as ei:
        model.validate(req)
    assert "视频/音频参考" in str(ei.value)
