"""语音情绪归一化:内联翻译 / 剥离 / 枚举收敛 / 普通括注保留 / 基类整合"""

from RH_ComfyUI.core.base.emotion import (
    to_inline_tag,
    to_enum_emotion,
    strip_inline_tags,
    translate_inline_zh_tags,
)
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest


def _req(prompt: str, mood: str | None = None) -> GenerationRequest:
    return GenerationRequest(task_type=TaskType.SPEECH, prompt=prompt, mood=mood)


# ── 纯函数 ──


def test_translate_inline_zh_tags():
    assert translate_inline_zh_tags("今天 [高兴] 天气真好") == "今天 [happy] 天气真好"
    assert translate_inline_zh_tags("你好 [whisper] 我想你了") == "你好 [whisper] 我想你了"
    # 普通中文括注不翻译
    assert translate_inline_zh_tags("我买了(苹果)") == "我买了(苹果)"


def test_to_inline_tag():
    assert to_inline_tag("开心") == "[happy]"
    assert to_inline_tag("[sad]") == "[sad]"
    assert to_inline_tag("excited") == "[excited]"


def test_strip_inline_tags():
    cleaned, stripped = strip_inline_tags("今天 [高兴] 天气真好")
    assert cleaned == "今天 天气真好"
    assert stripped == ["高兴"]
    # 普通中文括注保留,不误剥
    cleaned2, stripped2 = strip_inline_tags("我买了(苹果)三个")
    assert cleaned2 == "我买了(苹果)三个"
    assert stripped2 == []


def test_to_enum_emotion():
    allowed = ["happy", "sad", "calm", "whisper"]
    assert to_enum_emotion("开心", [], allowed) == "happy"
    assert to_enum_emotion("calm", [], allowed) == "calm"
    assert to_enum_emotion("随便描述", [], allowed) is None
    assert to_enum_emotion(None, ["高兴"], allowed) == "happy"


# ── 基类整合(经注册模型的 normalize;全离线) ──


def test_model_normalize_by_style():
    from RH_ComfyUI.models import discover_builtin_models
    from RH_ComfyUI.utils.backends import init_backends
    from RH_ComfyUI.core.routing.registry import model_registry

    init_backends()
    discover_builtin_models()

    fish = model_registry.get("fish_tts")
    assert fish is not None
    out = fish.normalize(_req("今天 [高兴] 天气真好"))
    assert out.prompt == "今天 [happy] 天气真好" and out.mood is None
    out = fish.normalize(_req("你好", "开心"))
    assert out.prompt == "[happy] 你好" and out.mood is None

    mmx = model_registry.get("minimax_t2a_speech")
    assert mmx is not None
    out = mmx.normalize(_req("今天 [高兴] 天气真好"))
    assert out.prompt == "今天 天气真好" and out.mood == "happy"
    assert mmx.normalize(_req("你好", "随便")).mood is None

    mimo = model_registry.get("mimo_tts")
    assert mimo is not None
    out = mimo.normalize(_req("今天 [高兴] 天气真好"))
    assert out.prompt == "今天 天气真好" and out.mood == "高兴"
