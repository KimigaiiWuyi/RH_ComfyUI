"""models — 开源模型实现层(全编程式,2026-07 起不再装载 YAML)

discover_builtin_models() 在 on_core_start 时被调用:
1. 实例化五个模态 defs.py 里的全部模型类(每个模型一个类,继承模态基类);
2. 模型实例注册进 core.routing.model_registry(单一事实来源);
3. NodeDef 目录(pipeline_registry)由 model_registry 派生 —— Adapter 执行 /
   AI 知识库 / HTTP 契约仍消费 NodeDef,但不再单独注册,避免两表漂移;
4. 加载 pip entry points 提供的外部模型(闭源接入途径之一)。
"""

from __future__ import annotations

from gsuid_core.logger import logger

from .asr import FishAsrModel
from .video import Wan22VideoModel, SeedanceVideoModel, HappyHorseVideoModel
from .speech import IndexTTS2Model


def discover_builtin_models() -> int:
    """装载全部内置模型进 model_registry;返回注册数量"""
    from .asr.defs import ALL_MODELS as ASR_MODELS
    from .image.defs import ALL_MODELS as IMAGE_MODELS
    from .music.defs import ALL_MODELS as MUSIC_MODELS
    from .video.defs import ALL_MODELS as VIDEO_MODELS
    from .speech.defs import ALL_MODELS as SPEECH_MODELS
    from ..core.routing.registry import model_registry, load_entry_point_models

    # NodeDef 目录(pipeline_registry)现由 model_registry 派生,无需再单独注册
    count = 0
    for cls in (*IMAGE_MODELS, *MUSIC_MODELS, *SPEECH_MODELS, *VIDEO_MODELS, *ASR_MODELS):
        model_registry.register(cls())
        count += 1

    external = load_entry_point_models()
    if external:
        logger.info(f"[models] 已加载 {external} 个外部(entry points)模型")
    logger.info(f"[models] 内置模型装载完成: {count} 个")
    return count + external


__all__ = [
    "discover_builtin_models",
    "SeedanceVideoModel",
    "Wan22VideoModel",
    "HappyHorseVideoModel",
    "IndexTTS2Model",
    "FishAsrModel",
]
