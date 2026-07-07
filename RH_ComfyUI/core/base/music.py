"""MusicGenerationBase — 音乐生成模态基类"""

from __future__ import annotations

from .errors import ValidationError
from .generation import AIGCGenerationBase
from ..schema.request import TaskType, GenerationRequest


class MusicGenerationBase(AIGCGenerationBase):
    """音乐生成模态基类"""

    modality: TaskType = TaskType.MUSIC

    # ── 模态级能力开关 ──
    supports_lyrics: bool = False  # negative_prompt 字段承载歌词(现状约定)
    max_duration_seconds: int = 240
    max_lyrics_length: int = 4000

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)
        if request.duration and request.duration > self.max_duration_seconds:
            raise ValidationError(
                f"{self.display_name} 音乐时长上限 {self.max_duration_seconds} 秒,当前 {request.duration} 秒"
            )
        if request.negative_prompt and self.supports_lyrics and len(request.negative_prompt) > self.max_lyrics_length:
            raise ValidationError(f"歌词长度超过 {self.display_name} 上限 {self.max_lyrics_length} 字")


__all__ = ["MusicGenerationBase"]
