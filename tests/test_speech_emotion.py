"""语音情绪归一化:只认显式情绪块 <<EMO>>、字面括号忽略、枚举收敛、基类整合"""

from RH_ComfyUI.core.base.emotion import (
    to_inline_tag,
    to_enum_emotion,
    render_inline_markers,
    extract_emotion_markers,
)
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest


def _req(prompt: str, mood: str | None = None) -> GenerationRequest:
    return GenerationRequest(task_type=TaskType.SPEECH, prompt=prompt, mood=mood)


# ── 纯函数 ──


def test_render_inline_markers():
    # 情绪块就地展开为 [english]（中→英）
    assert render_inline_markers("今天 <<EMO: 开心>> 天气真好") == "今天 [happy] 天气真好"
    assert render_inline_markers("你好 <<EMO: whisper>> 我想你了") == "你好 [whisper] 我想你了"
    # 字面括号（复制/手打的 []/【】）一律不动
    assert render_inline_markers("我买了[苹果]和【重要】") == "我买了[苹果]和【重要】"


def test_extract_emotion_markers():
    cleaned, labels = extract_emotion_markers("今天 <<EMO: 开心>> 天气真好")
    assert cleaned == "今天 天气真好"
    assert labels == ["开心"]
    # 普通括号文本不受影响
    cleaned2, labels2 = extract_emotion_markers("普通[文本]和【括注】不受影响")
    assert cleaned2 == "普通[文本]和【括注】不受影响"
    assert labels2 == []


def test_to_inline_tag():
    assert to_inline_tag("开心") == "[happy]"
    assert to_inline_tag("[sad]") == "[sad]"
    assert to_inline_tag("excited") == "[excited]"


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
    # 情绪块 → 内联展开(句中定位)
    out = fish.normalize(_req("今天 <<EMO: 开心>> 天气真好"))
    assert out.prompt == "今天 [happy] 天气真好" and out.mood is None
    # 字面 [开心] 是普通文本,不翻译
    out = fish.normalize(_req("字面 [开心] 不翻译"))
    assert out.prompt == "字面 [开心] 不翻译" and out.mood is None
    # 结构化情绪 → 句首
    out = fish.normalize(_req("你好", "开心"))
    assert out.prompt == "[happy] 你好" and out.mood is None

    mmx = model_registry.get("minimax_t2a_speech")
    assert mmx is not None
    out = mmx.normalize(_req("<<EMO: 开心>> 你好"))
    assert out.prompt == "你好" and out.mood == "happy"
    # 字面括号不剥离
    assert mmx.normalize(_req("[开心] 你好")).prompt == "[开心] 你好"
    assert mmx.normalize(_req("你好", "随便")).mood is None

    mimo = model_registry.get("mimo_tts")
    assert mimo is not None
    out = mimo.normalize(_req("<<EMO: 开心>> 你好"))
    assert out.prompt == "你好" and out.mood == "开心"
