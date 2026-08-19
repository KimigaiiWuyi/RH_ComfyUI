"""Seedance 历史单按供应商 raw usage 回算积分(不碰钱包/数据库)。"""

from RH_ComfyUI.core.billing.reconcile import (
    usage_from_vendor_raw,
    request_bits_from_record,
    actual_points_for_seedance_record,
)

_ARK_SAMPLE = {
    "id": "cgt-20260819182134-227sm",
    "model": "doubao-seedance-2-0-260128",
    "status": "succeeded",
    "content": {"video_url": "https://example.com/v.mp4"},
    "usage": {"completion_tokens": 488025, "total_tokens": 488025},
    "resolution": "1080p",
    "ratio": "16:9",
    "duration": 5,
    "framespersecond": 24,
    "generate_audio": True,
}


def test_usage_from_ark_query_payload():
    u = usage_from_vendor_raw(_ARK_SAMPLE)
    assert u is not None
    assert u["total_tokens"] == 488025


def test_usage_from_gateway_envelope():
    raw = {"code": 200, "data": {"tokenUsage": {"totalTokens": 488025}}}
    u = usage_from_vendor_raw(raw)
    assert u is not None
    assert u.get("totalTokens") == 488025


def test_usage_from_truncated_json_string():
    raw = '{"id":"cgt-1","usage":{"completion_tokens":488025,"total_tokens":488025...[TRUNCATED]'
    u = usage_from_vendor_raw(raw)
    assert u is not None
    assert u["completion_tokens"] == 488025


def test_content_video_url_counts_as_input():
    bits = request_bits_from_record(
        resolution="1080p",
        duration_seconds=5,
        request_body={
            "model": "doubao-seedance-2-0-260128",
            "resolution": "1080p",
            "duration": 5,
            "content": [
                {"type": "text", "text": "edit"},
                {"type": "video_url", "video_url": {"url": "https://example.com/in.mp4"}},
            ],
        },
    )
    assert bits["has_input"] is True
    assert bits["resolution"] == "1080p"


def test_actual_points_seedance2_1080p_with_input():
    # 488025 * 31 / 1e6 → 15.13 元 = 1513
    pts = actual_points_for_seedance_record(
        task_name="seedance2",
        raw_response=_ARK_SAMPLE,
        resolution="1080p",
        duration_seconds=5,
        request_body={
            "resolution": "1080p",
            "duration": 5,
            "content": [{"type": "video_url", "video_url": {"url": "https://x"}}],
        },
    )
    assert pts == 1513


def test_actual_points_seedance25_1080p_with_input():
    # 488025 * 46 / 1e6 → 22.45 元 = 2245
    pts = actual_points_for_seedance_record(
        task_name="seedance2.5",
        raw_response=_ARK_SAMPLE,
        resolution="1080p",
        duration_seconds=5,
        request_body={"content": [{"type": "video_url", "video_url": {"url": "https://x"}}]},
    )
    assert pts == 2245


def test_token_heuristic_marks_input_when_wire_lost_refs():
    """request_body 被 wire 覆盖且看不到参考视频时,token 约为 2 倍输出 → 按有输入。"""
    pts = actual_points_for_seedance_record(
        task_name="seedance2",
        raw_response=_ARK_SAMPLE,
        resolution="1080p",
        duration_seconds=5,
        request_body={"resolution": "1080p", "duration": 5, "content": [{"type": "text", "text": "hi"}]},
    )
    assert pts == 1513


def test_unknown_model_returns_none():
    assert (
        actual_points_for_seedance_record(
            task_name="seedance2_fast",
            raw_response=_ARK_SAMPLE,
            resolution="720p",
        )
        is None
    )


def test_no_usage_returns_none():
    assert (
        actual_points_for_seedance_record(
            task_name="seedance2",
            raw_response={"id": "x", "status": "succeeded"},
            resolution="720p",
        )
        is None
    )
