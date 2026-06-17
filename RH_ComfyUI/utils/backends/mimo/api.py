"""XiaoMi MiMo TTS API 客户端 — 封装 MiMo-V2.5-TTS 语音合成接口"""

from __future__ import annotations

import base64
import asyncio
from typing import Any, Dict, List, Union, Optional

import aiohttp

from gsuid_core.logger import logger

from ....rh_config.comfyui_config import SERVICE_CONFIG


class MIMOAPI:
    """XiaoMi MiMo TTS API 客户端

    封装 MiMo-V2.5-TTS 系列语音合成接口，支持：
    - 预置音色语音合成（mimo-v2.5-tts）
    - 文本描述音色设计（mimo-v2.5-tts-voicedesign）
    - 音频样本音色复刻（mimo-v2.5-tts-voiceclone）
    """

    # 支持的模型列表
    MODELS: List[str] = [
        "mimo-v2.5-tts",
        "mimo-v2.5-tts-voicedesign",
        "mimo-v2.5-tts-voiceclone",
    ]

    # 情绪/风格标签映射（用户友好名 → 标签）
    STYLE_TAGS: Dict[str, str] = {
        "happy": "开心",
        "sad": "悲伤",
        "angry": "愤怒",
        "fearful": "恐惧",
        "surprised": "惊讶",
        "excited": "兴奋",
        "calm": "平静",
        "cold": "冷漠",
        "gentle": "温柔",
        "cool": "高冷",
        "lively": "活泼",
        "serious": "严肃",
        "lazy": "慵懒",
        "playful": "俏皮",
        "deep": "深沉",
        "capable": "干练",
        "sharp": "凌厉",
        "magnetic": "磁性",
        "mellow": "醇厚",
        "clear": "清亮",
        "ethereal": "空灵",
        "young": "稚嫩",
        "old": "苍老",
        "sweet": "甜美",
        "hoarse": "沙哑",
    }

    base_url: str = "https://api.xiaomimimo.com/v1"

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    @property
    def api_key(self) -> str:
        """动态读取 API Key，避免模块导入时配置未生效"""
        return SERVICE_CONFIG.get_config("MIMO_apikey").data or ""

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    async def _request(
        self,
        json: Dict[str, Any],
        max_retries: int = 3,
    ) -> Union[Dict[str, Any], int]:
        """带重试机制的 HTTP 请求"""
        fail_count = 0

        while fail_count < max_retries:
            try:
                if not self.api_key:
                    logger.warning("[MiMo] 未配置 API Key，将无法请求！")
                    return -1

                token_prefix = self.api_key[:6]
                logger.info(f"[MiMo] 请求: POST {self.chat_url}, token_prefix={token_prefix}***")

                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.chat_url,
                        headers=self._headers(),
                        json=json,
                    ) as resp:
                        logger.info(f"[MiMo] 响应状态: {resp.status}")

                        if resp.status != 200:
                            # 读取错误响应体以便排查
                            try:
                                error_body = await resp.text()
                                logger.warning(f"[MiMo] 请求失败: {resp.status}, 响应: {error_body[:500]}")
                            except Exception:
                                logger.warning(f"[MiMo] 请求失败: {resp.status}")

                            # 401/403 鉴权错误不重试
                            if resp.status in (401, 403):
                                logger.error("[MiMo] API Key 无效或未配置，请在 Web 控制台配置 MIMO_apikey")
                                return resp.status

                            if resp.status == 429:
                                logger.info("[MiMo] 请求过于频繁(429)，等待60秒后重试...")
                                await asyncio.sleep(60)
                                continue

                            fail_count += 1
                            logger.warning(f"[MiMo] 重试 ({fail_count}/{max_retries})")
                            continue

                        resp_data = await resp.json()
                        logger.debug(f"[MiMo] 响应数据keys: {list(resp_data.keys())}")

                        # 检查错误
                        error = resp_data.get("error")
                        if error:
                            error_msg = error.get("message", "未知错误")
                            logger.error(f"[MiMo] API 错误: {error_msg}")
                            fail_count += 1
                            continue

                        return resp_data

            except Exception as e:
                logger.warning(f"[MiMo] 请求异常: {e}, 重试 ({fail_count + 1}/{max_retries})")
                fail_count += 1
                await asyncio.sleep(1)
                continue

        logger.error("[MiMo] 请求重试耗尽，最终失败")
        return 500

    async def generate_speech(
        self,
        text: str,
        mood: Optional[str] = None,
        reference_audio: Optional[bytes] = None,
        model: Optional[str] = None,
        audio_format: str = "wav",
        voice: Optional[str] = None,
    ) -> Union[bytes, int]:
        """MiMo TTS 语音合成

        Args:
            text: 待合成的文本（放在 assistant 消息中）
            mood: 情绪/风格控制指令（放在 user 消息中），支持自然语言描述或风格标签
            reference_audio: 参考音频 bytes（音色复刻模式）
            model: 模型名称，默认自动选择：
                   - 有 reference_audio → mimo-v2.5-tts-voiceclone
                   - 无 reference_audio → mimo-v2.5-tts
            audio_format: 输出音频格式（wav/pcm16/mp3）
            voice: 预置音色名称（仅 mimo-v2.5-tts 模型）

        Returns:
            音频字节数据 或 错误状态码
        """
        # 自动选择模型
        if model is None:
            if reference_audio is not None:
                model = "mimo-v2.5-tts-voiceclone"
            else:
                model = "mimo-v2.5-tts"

        if model not in self.MODELS:
            logger.warning(f"[MiMo] 未知模型 {model}，回退到 mimo-v2.5-tts")
            model = "mimo-v2.5-tts"

        logger.info(f"[MiMo] 开始语音合成: model={model}, text={text[:50]}..., mood={mood}")

        # 构建 messages
        messages: List[Dict[str, str]] = []

        # user 消息：情绪/风格控制（可选）
        if mood:
            messages.append(
                {
                    "role": "user",
                    "content": mood,
                }
            )
        elif model == "mimo-v2.5-tts-voicedesign":
            # voicedesign 模式下 user 消息为必填
            messages.append(
                {
                    "role": "user",
                    "content": "用自然流畅的语调朗读",
                }
            )

        # assistant 消息：待合成文本（必须）
        messages.append(
            {
                "role": "assistant",
                "content": text,
            }
        )

        # 构建请求体
        request_body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "audio": {
                "format": audio_format,
            },
        }

        # 音色复刻：传入参考音频
        if reference_audio is not None and model == "mimo-v2.5-tts-voiceclone":
            # 检测音频格式
            mime_type = "audio/mpeg"
            if reference_audio[:4] == b"RIFF":
                mime_type = "audio/wav"
            elif reference_audio[:3] == b"ID3" or reference_audio[0:2] == b"\xff\xfb":
                mime_type = "audio/mpeg"

            b64_audio = base64.b64encode(reference_audio).decode("utf-8")
            request_body["audio"]["voice"] = f"data:{mime_type};base64,{b64_audio}"

        # 预置音色
        if voice and model == "mimo-v2.5-tts":
            request_body["audio"]["voice"] = voice

        # 发送请求
        resp = await self._request(json=request_body)

        if isinstance(resp, int):
            logger.error(f"[MiMo] 语音合成失败，状态码: {resp}")
            return resp

        # 解析响应
        try:
            choices = resp.get("choices", [])
            if not choices:
                logger.error(f"[MiMo] 响应中没有 choices: {resp}")
                return 500

            message = choices[0].get("message", {})
            audio_data = message.get("audio", {})

            if not audio_data:
                logger.error(f"[MiMo] 响应中没有 audio 数据: {message}")
                return 500

            # 提取音频数据（base64 编码）
            audio_b64 = audio_data.get("data", "")
            if not audio_b64:
                logger.error("[MiMo] audio.data 为空")
                return 500

            audio_bytes = base64.b64decode(audio_b64)
            logger.info(f"[MiMo] 语音合成成功: {len(audio_bytes)} bytes")
            return audio_bytes

        except Exception as e:
            logger.error(f"[MiMo] 响应解析失败: {e}")
            return 500


# 全局单例
mimo_api = MIMOAPI()
