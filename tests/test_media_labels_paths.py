"""媒体代号路径回归 — seedance2 OC / banana 扁平 / happyhorse / first_last

验证:
1. ensure_media_ref_labels 不破坏 Seedance OC 语义(不双计 media)
2. OC text 段本身不含结构化代号,Ark 侧仍只注入一次【图片N】
3. 扁平 banana/gpt-image 路径 ensure 为 no-op
4. happyhorse 能把 [参考图片N] 改写为 [Image N]
"""

from __future__ import annotations

from RH_ComfyUI.utils.core.types import ContentItem, ContentItemType, image_ref
from RH_ComfyUI.utils.core.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.mappers.video import interpolate_prompt_refs
from RH_ComfyUI.utils.core.media_labels import ensure_media_ref_labels
from RH_ComfyUI.utils.backends.seedance.classify import classify_video_spec
from RH_ComfyUI.utils.backends.happyhorse.classify import classify_happyhorse, rewrite_prompt_for_r2v

png_a = b"\x89PNG\r\n\x1a\n" + b"A" * 200
png_b = b"\x89PNG\r\n\x1a\n" + b"B" * 200


def _oc_swap():
    return [
        ContentItem(type=ContentItemType.TEXT, text="将"),
        ContentItem(type=ContentItemType.IMAGE, media=image_ref(data=png_a)),
        ContentItem(type=ContentItemType.TEXT, text="中的角色替换为"),
        ContentItem(type=ContentItemType.IMAGE, media=image_ref(data=png_b)),
        ContentItem(type=ContentItemType.TEXT, text="，其他不变"),
    ]


def _ark_like_text(spec) -> str:
    """模拟 Ark content[] 文本合并:segments 原文 + 媒体位注入【图片N】。"""
    parts: list[str] = []
    img = 0
    for s in spec.ordered_segments:
        if s.kind == "text" and s.text:
            parts.append(s.text)
        elif s.kind == "media" and s.media is not None:
            img += 1
            parts.append(f"【图片{img}】")
    return "".join(parts)


def test_seedance_oc_hollow_prompt_rebuild_no_double_media():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="将 中的角色替换为 ，其他不变",
        ordered_content=_oc_swap(),
        images=[],
        params={"frame_mode": "reference"},
    )
    req = ensure_media_ref_labels(req)
    assert req.prompt == "将[参考图片1]中的角色替换为[参考图片2]，其他不变"
    assert len(req.images) == 2

    spec = classify_video_spec(req)
    assert len(spec.media) == 2
    assert len(spec.ordered_segments) == 5
    joined_text = "".join(s.text or "" for s in spec.ordered_segments if s.kind == "text")
    assert "参考图片" not in joined_text
    assert _ark_like_text(spec) == "将【图片1】中的角色替换为【图片2】，其他不变"


def test_seedance_oc_with_frontend_labels_still_single_inject():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="将[参考图片1]中的角色替换为[参考图片2]，其他不变",
        ordered_content=_oc_swap(),
        images=[],
        params={"frame_mode": "reference"},
    )
    req = ensure_media_ref_labels(req)
    assert req.prompt == "将[参考图片1]中的角色替换为[参考图片2]，其他不变"
    spec = classify_video_spec(req)
    assert len(spec.media) == 2
    assert "[参考图片1]" in spec.prompt and "[参考图片2]" in spec.prompt
    assert _ark_like_text(spec) == "将【图片1】中的角色替换为【图片2】，其他不变"


def test_seedance_dual_send_oc_and_images_no_double_count():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="x",
        ordered_content=_oc_swap(),
        images=[png_a, png_b],
        params={"frame_mode": "reference"},
    )
    req = ensure_media_ref_labels(req)
    spec = classify_video_spec(req)
    assert len(spec.media) == 2


def test_banana_flat_ensure_noop():
    req = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="将[参考图片1]中的角色替换为[参考图片2]，其他不变",
        images=[png_a, png_b],
        ordered_content=[],
    )
    out = ensure_media_ref_labels(req)
    assert out.prompt == "将[参考图片1]中的角色替换为[参考图片2]，其他不变"
    assert out.images == [png_a, png_b]


def test_wan_uses_structured_and_bare_labels_for_interpolation():
    assert "首帧图" in interpolate_prompt_refs("[参考图片1] 走向 [参考图片2]", image_count=2)
    assert "尾帧图" in interpolate_prompt_refs("[参考图片1] 走向 [参考图片2]", image_count=2)
    # 旧裸写法仍兼容
    assert "首帧图" in interpolate_prompt_refs("图片1 走向 图片2", image_count=2)


def test_happyhorse_r2v_from_structured_and_bare():
    assert rewrite_prompt_for_r2v("看[参考图片1]和[参考图片2]") == "看[Image 1]和[Image 2]"
    assert rewrite_prompt_for_r2v("图片1中的女孩与图片 2") == "[Image 1]中的女孩与[Image 2]"

    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="将 中的角色替换为 ",
        ordered_content=_oc_swap(),
        images=[],
        params={"frame_mode": "reference"},
    )
    req = ensure_media_ref_labels(req)
    rewritten = rewrite_prompt_for_r2v(req.prompt)
    assert rewritten == "将[Image 1]中的角色替换为[Image 2]，其他不变"
    spec = classify_happyhorse(req)
    assert len(spec.media) == 2


def test_seedance_first_last_flat_ensure_noop():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="从[参考图片1]变到[参考图片2]",
        images=[png_a, png_b],
        ordered_content=[],
        params={"frame_mode": "first_last"},
    )
    out = ensure_media_ref_labels(req)
    assert out.prompt == "从[参考图片1]变到[参考图片2]"
    spec = classify_video_spec(out)
    assert len(spec.media) == 2
