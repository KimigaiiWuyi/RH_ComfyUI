"""Fish Audio 官方 API 客户端 — S2 系列 TTS 与快速音色克隆

只对接官方公开端点(api.fish.audio)。凭证与模型档位从配置动态读取,
中途改配置即时生效(不缓存到实例,见 @property)。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Union, Optional

import aiohttp

from gsuid_core.logger import logger

from ....rh_config.comfyui_config import SERVICE_CONFIG

# 仅对接官方公开端点(不额外暴露地址配置)。
_BASE_URL = "https://api.fish.audio"

# 允许的模型档位(header 里的 model 值)。s2.1-pro 在官方免费期内不计费,作默认。
# 刻意不含营销名 "s2.1-pro-free"(实测线上判 Unknown model):这样即便旧配置里存的是
# 它,property.model 里 `value in KNOWN_MODELS` 不成立 → 自动回落到 s2.1-pro,免手改。
DEFAULT_MODEL = "s2.1-pro"
KNOWN_MODELS = ("s2.1-pro", "s2-pro", "s1")

# 音色训练中的状态:处于这些状态需等待就绪后才能用于合成。
# 快速克隆通常即时可用;偶发未就绪时轮询兜底,超预算仍未就绪则尽力尝试。
_NOT_READY_STATES = frozenset({"created", "training", "pending", "processing"})


def _audio_content_type(audio: bytes) -> tuple[str, str]:
    """按文件头嗅探音频类型;返回 (filename, content_type)"""
    if audio[:4] == b"RIFF":
        return "reference.wav", "audio/wav"
    return "reference.mp3", "audio/mpeg"


class FishAudioAPI:
    """Fish Audio TTS + 音色克隆客户端"""

    base_url: str = _BASE_URL

    @property
    def api_key(self) -> str:
        """动态读取,避免导入期配置未就绪把空值缓存到进程退出"""
        return SERVICE_CONFIG.get_config("FishAudio_apikey").data or ""

    @property
    def model(self) -> str:
        """默认合成档位;非法/空值回退到免费档"""
        value = SERVICE_CONFIG.get_config("FishAudio_Model").data or ""
        return value if value in KNOWN_MODELS else DEFAULT_MODEL

    def _auth_header(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def create_voice_model(self, audio: bytes, title: str) -> Optional[str]:
        """用参考音频快速克隆一个私有音色,返回可复用的音色 id

        train_mode=fast 即时可用;visibility=unlist 不进公共音色库。
        失败返回 None(由上层决定是否停止,避免回退随机音色)。
        """
        if not self.api_key:
            logger.warning("[FishAudio] 未配置 API Key,无法克隆音色")
            return None

        filename, content_type = _audio_content_type(audio)
        form = aiohttp.FormData()
        form.add_field("type", "tts")
        form.add_field("title", title)
        form.add_field("train_mode", "fast")
        form.add_field("visibility", "unlist")
        form.add_field("enhance_audio_quality", "true")
        form.add_field("voices", audio, filename=filename, content_type=content_type)

        url = f"{self.base_url}/model"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self._auth_header(), data=form) as resp:
                    if resp.status not in (200, 201):
                        body = await resp.text()
                        logger.warning(f"[FishAudio] 克隆音色失败: {resp.status}, {body[:300]}")
                        return None
                    data = await resp.json()
        except aiohttp.ClientError as e:
            logger.warning(f"[FishAudio] 克隆音色网络异常: {e}")
            return None

        model_id = data.get("_id")
        if not (isinstance(model_id, str) and model_id):
            logger.warning(f"[FishAudio] 克隆响应缺少 _id: {list(data.keys())}")
            return None

        state = data.get("state")
        if isinstance(state, str) and state in _NOT_READY_STATES:
            await self._wait_ready(model_id)
        logger.info(f"[FishAudio] 克隆音色成功: model_id={model_id}")
        return model_id

    async def _model_state(self, model_id: str) -> Optional[str]:
        """查询音色训练状态;查询失败返回 None(视作不再阻塞)"""
        url = f"{self.base_url}/model/{model_id}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._auth_header()) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
        except aiohttp.ClientError as e:
            logger.warning(f"[FishAudio] 查询音色状态异常: {e}")
            return None
        state = data.get("state")
        return state if isinstance(state, str) else None

    async def _wait_ready(self, model_id: str, attempts: int = 8, interval: float = 1.5) -> None:
        """轮询到音色离开训练态;超预算仍未就绪则退出让调用方尽力尝试"""
        for _ in range(attempts):
            await asyncio.sleep(interval)
            state = await self._model_state(model_id)
            if state is None or state not in _NOT_READY_STATES:
                return
        logger.warning(f"[FishAudio] 音色 {model_id} 未在预期内就绪,仍尝试使用")

    async def tts(
        self,
        text: str,
        reference_id: Optional[str] = None,
        model: Optional[str] = None,
        speed: float = 1.0,
    ) -> Union[bytes, str]:
        """合成语音,返回音频字节;失败返回**人话错误信息**(str),供上层直接透传给用户

        reference_id 为空时用档位内置默认音色;情绪标签已在正文内联(基类处理)。
        """
        if not self.api_key:
            logger.warning("[FishAudio] 未配置 API Key,将无法请求")
            return "未配置 Fish Audio API Key(FishAudio_apikey)"

        engine = model if model in KNOWN_MODELS else self.model
        body: Dict[str, Any] = {
            "text": text,
            "format": "mp3",
            "mp3_bitrate": 128,
            "normalize": True,
        }
        if reference_id:
            body["reference_id"] = reference_id
        if speed and speed != 1.0:
            body["prosody"] = {"speed": speed}

        headers = {**self._auth_header(), "Content-Type": "application/json", "model": engine}
        url = f"{self.base_url}/v1/tts"
        logger.info(f"[FishAudio] 合成: model={engine}, cloned={bool(reference_id)}, text={text[:50]}...")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=body) as resp:
                    if resp.status != 200:
                        detail = (await resp.text())[:200]
                        logger.warning(f"[FishAudio] 合成失败: {resp.status}, {detail}")
                        # 400 多为档位不被账号支持(如 Unknown model)→ 指向配置项,可自助修
                        hint = f"(可在 Web 控制台改 FishAudio_Model 档位,当前 {engine})" if resp.status == 400 else ""
                        return f"HTTP {resp.status}: {detail}{hint}"
                    audio = await resp.read()
        except aiohttp.ClientError as e:
            logger.warning(f"[FishAudio] 合成网络异常: {e}")
            return f"网络异常: {e}"

        if not audio:
            logger.warning("[FishAudio] 合成成功但未返回音频")
            return "上游返回空音频"
        logger.info(f"[FishAudio] 合成成功: {len(audio)} bytes")
        return audio


# 全局单例
fishaudio_api = FishAudioAPI()
