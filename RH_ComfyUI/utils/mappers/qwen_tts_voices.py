"""Qwen-Audio TTS 系统音色。

各模型的音色不能混用。基础音色(克隆)不在这里。
"""

from __future__ import annotations

QWEN_TTS_FLASH = "qwen-audio-3.0-tts-flash"
QWEN_TTS_PLUS = "qwen-audio-3.0-tts-plus"
QWEN_TTS_31 = "qwen-audio-3.1-tts-flash"

QWEN_TTS_MODELS: tuple[str, ...] = (QWEN_TTS_FLASH, QWEN_TTS_PLUS, QWEN_TTS_31)

# 3.0 flash 系统音色。默认是指南里的 longanhuan_v3.6。
QWEN_FLASH_VOICES: tuple[str, ...] = (
    "longanfengyue",
    "longanyuanfei",
    "longanlingxi",
    "longanxiaoxin",
    "longanhuan_v3.6",
    "longjielidou_v3.6",
    "longpaopao_v3.6",
    "longhuohuo_v3.6",
    "longchuanshu_v3.6",
    "loongmary",
    "loongeva_v3.6",
    "loongjohn",
)
QWEN_FLASH_DEFAULT_VOICE = "longanhuan_v3.6"

# 3.0 plus 系统音色只有这两个旗舰。
QWEN_PLUS_VOICES: tuple[str, ...] = ("longanlingxin", "longanlufeng")
QWEN_PLUS_DEFAULT_VOICE = "longanlingxin"

# 3.1 系统音色。默认 longanhuan_v3.1。
QWEN_31_VOICES: tuple[str, ...] = (
    "longanhuan_v3.1",
    "longanlingxin_v3.1",
    "longanfengyue_v3.1",
    "xunanchuan_v3.1",
    "yuxiaoyun_v3.1",
    "qiaoxiaojiao_v3.1",
    "xiaxiaochen_v3.1",
    "anmingyuan_v3.1",
    "wenhuaiqing_v3.1",
    "anxiaolan_v3.1",
    "xieshurou_v3.1",
    "baiqinglan_v3.1",
    "xuyuyuan_v3.1",
    "anruorou_v3.1",
    "wenhuaizhi_v3.1",
    "xiaoxingzhi_v3.1",
    "guyunshu_v3.1",
    "huozhuoshi_v3.1",
    "yeqinghe_v3.1",
    "yunhuanhuan_v3.1",
    "xuxiaoqiao_v3.1",
    "baianran_v3.1",
    "xuyanchu_v3.1",
    "yezhiqing_v3.1",
    "andi_v3.1",
    "anyuqing_v3.1",
    "Emily_v3.1",
    "Luna_v3.1",
    "Eric_v3.1",
    "Luca_v3.1",
    "Abby_v3.1",
    "Annie_v3.1",
    "Ava_v3.1",
    "Beth_v3.1",
    "Betty_v3.1",
    "Cally_v3.1",
    "Cindy_v3.1",
    "Donna_v3.1",
    "Andy_v3.1",
    "Brian_v3.1",
    "David_v3.1",
    "longanyuanfei_v3.1",
    "longjielidou_v3.1",
    "longanlingxi_v3.1",
    "longhuohuo_v3.1",
    "longyingtao_v3.1",
    "longanya_v3.1",
    "longwan_v3.1",
    "longxing_v3.1",
    "longhua_v3.1",
    "longhan_v3.1",
    "longanzhi_v3.1",
    "longzhe_v3.1",
    "longanyang_v3.1",
    "libai_v3.1",
    "longling_v3.1",
    "longniuniu_v3.1",
    "longshanshan_v3.1",
    "longpaopao_v3.1",
    "loongstella_v3.1",
    "longyuan_v3.1",
    "longmiao_v3.1",
    "longsanshu_v3.1",
    "longanli_v3.1",
    "longanwen_v3.1",
    "longanlang_v3.1",
    "longxiaoxia_v3.1",
    "longanchong_v3.1",
)
QWEN_31_DEFAULT_VOICE = "longanhuan_v3.1"

QWEN_VOICES_BY_MODEL: dict[str, tuple[str, ...]] = {
    QWEN_TTS_FLASH: QWEN_FLASH_VOICES,
    QWEN_TTS_PLUS: QWEN_PLUS_VOICES,
    QWEN_TTS_31: QWEN_31_VOICES,
}
QWEN_DEFAULT_VOICE_BY_MODEL: dict[str, str] = {
    QWEN_TTS_FLASH: QWEN_FLASH_DEFAULT_VOICE,
    QWEN_TTS_PLUS: QWEN_PLUS_DEFAULT_VOICE,
    QWEN_TTS_31: QWEN_31_DEFAULT_VOICE,
}

__all__ = [
    "QWEN_31_DEFAULT_VOICE",
    "QWEN_31_VOICES",
    "QWEN_DEFAULT_VOICE_BY_MODEL",
    "QWEN_FLASH_DEFAULT_VOICE",
    "QWEN_FLASH_VOICES",
    "QWEN_PLUS_DEFAULT_VOICE",
    "QWEN_PLUS_VOICES",
    "QWEN_TTS_31",
    "QWEN_TTS_FLASH",
    "QWEN_TTS_MODELS",
    "QWEN_TTS_PLUS",
    "QWEN_VOICES_BY_MODEL",
]
