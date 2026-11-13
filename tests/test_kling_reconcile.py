"""可灵历史单按落库 wire 载荷回算积分(不碰钱包/数据库)。

覆盖视频(settings + contents)与图片(五种端点形状)两条线,
验证「1 元 = 100 积分」新口径下从 request_body_json 还原出的正确值。
"""

from RH_ComfyUI.core.billing.reconcile import actual_points_for_kling_record


def _video_body(*contents, **settings):
    body: dict[str, object] = {"settings": settings, "options": {}}
    if contents:
        body["contents"] = [{"type": "prompt", "text": "猫"}] + list(contents)
    else:
        body["prompt"] = "猫"
    return body


def _video(model: str, body: dict, extra: dict | None = None) -> int | None:
    return actual_points_for_kling_record(
        task_name=model,
        request_body=body,
        extra_params=extra or {},
    )


def _image(model: str, body: dict) -> int | None:
    return actual_points_for_kling_record(task_name=model, request_body=body, extra_params={})


def test_v40_text_1080p_10s_is_1500():
    """事故单:kling-4.0 1080P 10s 文生 = 15 元 = 1500 积分(旧库记成 210)。"""
    body = _video_body(resolution="1080p", duration=10, aspect_ratio="16:9")
    assert _video("kling-4.0", body) == 1500


def test_v40_i2v_uses_plain_rate():
    """图片关键帧不算参考视频,仍走无输入价。"""
    body = _video_body(
        {"type": "first_frame", "url": "<base64://a#1>"},
        resolution="720p",
        duration=5,
    )
    assert _video("kling-4.0", body) == 500  # 1.0 × 5


def test_v40_feature_video_bills_double_output():
    """有参考视频且输入≤输出:按输出×2 秒,720p 0.6/秒。"""
    body = _video_body(
        {"type": "feature_video", "url": "https://x/v.mp4"},
        resolution="720p",
        duration=5,
        audio="original",
    )
    assert _video("kling-4.0", body, {"input_video_duration": 5}) == 600  # 0.6 × (5×2)


def test_v40_longer_input_bills_input_plus_output():
    body = _video_body(
        {"type": "base_video", "url": "https://x/v.mp4"},
        resolution="1080p",
        duration=5,
        audio="native",
    )
    assert _video("kling-4.0", body, {"input_video_duration": 20}) == 2250  # 0.9 × (20+5)


def test_v40_missing_input_duration_defaults_to_output():
    body = _video_body(
        {"type": "feature_video", "url": "https://x/v.mp4"},
        resolution="720p",
        duration=5,
    )
    assert _video("kling-4.0", body) == 600  # 缺省按输入=输出 → 2×输出


def test_v40_flash_720p():
    body = _video_body(resolution="720p", duration=16, aspect_ratio="16:9")
    assert _video("kling-4.0-flash", body) == 960  # 0.6 × 16


def test_v30_standard_native_vs_omni_native():
    """3.0 有声:标准 i2v 1.2/秒,Omni 无参考有声 1.0/秒(两行不同价)。"""
    standard = _video_body(
        {"type": "first_frame", "url": "x"},
        {"type": "last_frame", "url": "y"},
        resolution="1080p",
        duration=5,
        audio="native",
    )
    assert _video("kling-3.0", standard) == 600  # 1.2 × 5
    omni = _video_body(
        {"type": "first_frame", "url": "x"},
        {"type": "refer_image", "url": "y"},
        resolution="1080p",
        duration=5,
        audio="native",
    )
    assert _video("kling-3.0", omni) == 500  # 1.0 × 5


def test_v30_force_omni_param():
    """只有首帧但 params.omni=True(走了 Omni 路径)→ 用 Omni 有声价。"""
    body = _video_body(
        {"type": "first_frame", "url": "x"},
        resolution="720p",
        duration=5,
        audio="native",
    )
    assert _video("kling-3.0", body) == 450  # 0.9 × 5(标准)
    assert _video("kling-3.0", body, {"omni": True}) == 400  # 0.8 × 5(Omni)


def test_v30_ref_video_rate():
    body = _video_body(
        {"type": "feature_video", "url": "https://x/v.mp4"},
        resolution="720p",
        duration=5,
        audio="off",
    )
    assert _video("kling-3.0", body) == 450  # 参考视频行 0.9 × 5


def test_v30_turbo():
    body = _video_body(resolution="1080p", duration=4, aspect_ratio="9:16")
    assert _video("kling-3.0-turbo", body) == 400  # 1.0 × 4


def test_o1_plain_and_ref():
    plain = _video_body(resolution="720p", duration=5, aspect_ratio="16:9")
    assert _video("kling-o1", plain) == 300  # 0.6 × 5
    ref = _video_body(
        {"type": "base_video", "url": "https://x/v.mp4"},
        resolution="720p",
        duration=5,
        audio="original",
    )
    assert _video("kling-o1", ref) == 450  # 0.9 × 5


def test_v26_voice_and_silent():
    voice = _video_body(
        {"type": "first_frame", "url": "x"},
        {"type": "voice", "voice_id": "v1"},
        resolution="1080p",
        duration=5,
        audio="native",
    )
    assert _video("kling-2.6", voice) == 600  # 音色 1.2 × 5
    silent = _video_body(resolution="720p", duration=5, aspect_ratio="16:9")
    assert _video("kling-2.6", silent) == 150  # 0.3 × 5


def test_v25_turbo_silent():
    body = _video_body(resolution="1080p", duration=10, aspect_ratio="16:9")
    assert _video("kling-2.5-turbo", body) == 500  # 0.5 × 10


def test_image_generations_t2i_i2i():
    t2i = {"model_name": "kling-v3", "resolution": "1k", "n": 1, "aspect_ratio": "16:9"}
    assert _image("kling-image-3.0", t2i) == 20
    i2i = {**t2i, "image": "<base64://a#1>"}
    assert _image("kling-image-3.0", i2i) == 20  # 3.0 i2i 同价 0.2
    v21 = {"model_name": "kling-v2-1", "resolution": "1k", "n": 2, "aspect_ratio": "16:9"}
    assert _image("kling-image-2.1", v21) == 20  # 0.1 × 2
    assert _image("kling-image-2.1", {**v21, "image": "<base64://a#1>"}) == 40  # 0.2 × 2


def test_image_omni_tiers():
    omni = {"model_name": "kling-v3-omni", "resolution": "4k", "n": 2, "result_type": "single"}
    assert _image("kling-image-3.0", omni) == 80  # 0.4 × 2
    o1 = {"model_name": "kling-image-o1", "resolution": "2k", "n": 3, "result_type": "single"}
    assert _image("kling-image-o1", o1) == 60  # 0.2 × 3
    series = {
        "model_name": "kling-v3-omni",
        "resolution": "1k",
        "result_type": "series",
        "series_amount": 5,
    }
    assert _image("kling-image-3.0", series) == 100  # 0.2 × 5
    auto = {**series, "series_amount": "auto"}
    assert _image("kling-image-3.0", auto) == 80  # auto 按 4 张 → 0.2 × 4


def test_image_multi_and_subject_and_expand():
    multi = {"model_name": "kling-v2-1", "subject_image_list": [{"subject_image": "x"}], "n": 2}
    assert _image("kling-image-2.1", multi) == 80  # 0.4 × 2
    subject = {"element_frontal_image": "<base64://a#1>"}
    assert _image("kling-image-common", subject) == 50  # 主体补全 0.5/次
    expand = {
        "image": "<base64://a#1>",
        "up_expansion_ratio": 0.1,
        "down_expansion_ratio": 0.1,
        "left_expansion_ratio": 0.1,
        "right_expansion_ratio": 0.1,
        "n": 3,
    }
    assert _image("kling-image-common", expand) == 60  # 0.2 × 3


def test_unknown_model_and_bad_body():
    assert _image("seedance2", {"model_name": "kling-v3"}) is None
    assert _video("kling-4.0", {}) is None  # settings 缺失 → duration 0
    assert actual_points_for_kling_record(task_name="kling-4.0", request_body="{bad", extra_params="") is None
    assert actual_points_for_kling_record(task_name="kling-4.0", request_body="", extra_params="") is None
