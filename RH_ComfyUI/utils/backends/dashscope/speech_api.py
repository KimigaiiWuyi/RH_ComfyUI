"""Qwen-Audio TTS 的 WebSocket 客户端。

控制台只有 HTTP 根地址,没有业务空间 ID,所以走同主机的旧路径
/api-ws/v1/inference。带业务空间 ID 的 maas 域名不是这项配置能表达的。
"""

from __future__ import annotations

import json
import uuid
import asyncio
from typing import Protocol, TypedDict
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from collections.abc import Callable, Awaitable

from gsuid_core.logger import logger

from .config import DASHSCOPE_DEFAULT_BASE_URL, dashscope_credentials

_TIMEOUT_S = 120.0
_MAX_FRAME = 32 * 1024 * 1024


class QwenSpeechSocket(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


QwenConnect = Callable[[str, str], Awaitable[QwenSpeechSocket]]


class _Header(TypedDict):
    action: str
    task_id: str
    streaming: str


class _Parameters(TypedDict, total=False):
    text_type: str
    voice: str
    format: str
    sample_rate: int
    volume: int
    rate: float
    pitch: int
    enable_ssml: bool
    instruction: str


class _RunPayload(TypedDict):
    task_group: str
    task: str
    function: str
    model: str
    parameters: _Parameters
    input: dict[str, str]


class _RunCommand(TypedDict):
    header: _Header
    payload: _RunPayload


class _TextInput(TypedDict):
    text: str


class _ContinuePayload(TypedDict):
    input: _TextInput


class _ContinueCommand(TypedDict):
    header: _Header
    payload: _ContinuePayload


class _FinishPayload(TypedDict):
    input: dict[str, str]


class _FinishCommand(TypedDict):
    header: _Header
    payload: _FinishPayload


class _QwenTtsWireBase(TypedDict):
    model: str
    voice: str
    format: str
    sample_rate: int
    rate: float
    text: str


class QwenTtsWire(_QwenTtsWireBase, total=False):
    instruction: str


@dataclass(frozen=True)
class QwenSpeechAudio:
    audio: bytes
    usage: dict[str, object]


def dashscope_tts_ws_url(base_url: str) -> str:
    raw = base_url.strip() or DASHSCOPE_DEFAULT_BASE_URL
    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    if scheme == "http":
        ws_scheme = "ws"
    elif scheme == "ws":
        ws_scheme = "ws"
    else:
        ws_scheme = "wss"
    path = parsed.path.rstrip("/")
    http_suffix = "/api/v1"
    ws_path = "/api-ws/v1/inference"
    if path.endswith(http_suffix):
        path = path[: -len(http_suffix)] + ws_path
    elif path.endswith(ws_path):
        pass
    elif not path:
        path = ws_path
    else:
        path = path + ws_path
    return urlunsplit((ws_scheme, parsed.netloc, path, "", ""))


def qwen_tts_wire_body(
    *,
    model: str,
    text: str,
    voice: str,
    rate: float,
    instruction: str,
) -> QwenTtsWire:
    body: QwenTtsWire = {
        "model": model,
        "voice": voice,
        "format": "mp3",
        "sample_rate": 22050,
        "rate": rate,
        "text": text,
    }
    if instruction:
        body["instruction"] = instruction
    return body


def _clamp_rate(speed: float) -> float:
    if speed < 0.5:
        return 0.5
    if speed > 2.0:
        return 2.0
    return speed


def _header(action: str, task_id: str) -> _Header:
    return {"action": action, "task_id": task_id, "streaming": "duplex"}


def _run_command(
    *,
    task_id: str,
    model: str,
    voice: str,
    rate: float,
    instruction: str,
) -> _RunCommand:
    parameters: _Parameters = {
        "text_type": "PlainText",
        "voice": voice,
        "format": "mp3",
        "sample_rate": 22050,
        "volume": 50,
        "rate": rate,
        "pitch": 1,
        "enable_ssml": False,
    }
    if instruction:
        parameters["instruction"] = instruction
    return {
        "header": _header("run-task", task_id),
        "payload": {
            "task_group": "audio",
            "task": "tts",
            "function": "SpeechSynthesizer",
            "model": model,
            "parameters": parameters,
            "input": {},
        },
    }


def _continue_command(task_id: str, text: str) -> _ContinueCommand:
    return {
        "header": _header("continue-task", task_id),
        "payload": {"input": {"text": text}},
    }


def _finish_command(task_id: str) -> _FinishCommand:
    return {"header": _header("finish-task", task_id), "payload": {"input": {}}}


def _as_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            out[key] = item
    return out


def _parse_event(text: str) -> dict[str, object]:
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("DashScope 语音事件不是 JSON") from exc
    blob = _as_dict(parsed)
    if blob is None:
        raise RuntimeError("DashScope 语音事件不是 JSON 对象")
    return blob


def _event_name(event: dict[str, object]) -> str:
    if "header" not in event:
        return ""
    header = _as_dict(event["header"])
    if header is None or "event" not in header:
        return ""
    name = header["event"]
    return name if isinstance(name, str) else ""


def _fail_message(event: dict[str, object]) -> str:
    if "header" not in event:
        return "DashScope 语音合成失败"
    header = _as_dict(event["header"])
    if header is None or "error_message" not in header:
        return "DashScope 语音合成失败"
    message = header["error_message"]
    if isinstance(message, str) and message.strip():
        return message.strip()
    return "DashScope 语音合成失败"


def _usage_from_event(event: dict[str, object]) -> dict[str, object]:
    if "payload" not in event:
        return {}
    payload = _as_dict(event["payload"])
    if payload is None or "usage" not in payload:
        return {}
    usage = _as_dict(payload["usage"])
    if usage is None:
        return {}
    out: dict[str, object] = {}
    for key in ("characters", "input_tokens", "output_tokens"):
        if key in usage:
            out[key] = usage[key]
    return out


async def _exchange(sock: QwenSpeechSocket, task_id: str, run: _RunCommand, text: str) -> QwenSpeechAudio:
    await sock.send(json.dumps(run, ensure_ascii=False))
    audio = bytearray()
    started = False
    while True:
        frame = await sock.recv()
        if isinstance(frame, bytes):
            audio.extend(frame)
            continue
        event = _parse_event(frame)
        name = _event_name(event)
        if name == "task-failed":
            raise RuntimeError(_fail_message(event))
        if name == "task-started" and not started:
            started = True
            # 服务端空闲大约 23 秒,文本发完立刻 finish。
            await sock.send(json.dumps(_continue_command(task_id, text), ensure_ascii=False))
            await sock.send(json.dumps(_finish_command(task_id), ensure_ascii=False))
            continue
        if name == "task-finished":
            if not audio:
                raise RuntimeError("DashScope 语音没有返回音频")
            from ....core.telemetry.wire_capture import set_vendor_raw

            set_vendor_raw(event)
            return QwenSpeechAudio(audio=bytes(audio), usage=_usage_from_event(event))


class _LiveSocket:
    def __init__(self, conn: object) -> None:
        from websockets.asyncio.client import ClientConnection

        if not isinstance(conn, ClientConnection):
            raise RuntimeError("DashScope 语音连接类型异常")
        self._conn = conn

    async def send(self, message: str) -> None:
        await self._conn.send(message)

    async def recv(self) -> str | bytes:
        frame = await self._conn.recv()
        if isinstance(frame, str):
            return frame
        if isinstance(frame, bytes):
            return frame
        if isinstance(frame, bytearray):
            return bytes(frame)
        raise RuntimeError("DashScope 语音帧类型异常")

    async def close(self) -> None:
        await self._conn.close()


async def _open_socket(url: str, api_key: str) -> QwenSpeechSocket:
    import websockets

    conn = await websockets.connect(
        url,
        additional_headers={"Authorization": f"Bearer {api_key}"},
        open_timeout=30,
        max_size=_MAX_FRAME,
    )
    return _LiveSocket(conn)


class DashScopeSpeechAPI:
    """每次调用再读 HappyHorse_*_dashscope,不在初始化时冻密钥。"""

    def __init__(self, connect: QwenConnect | None = None) -> None:
        self._connect = connect

    def is_configured(self) -> bool:
        creds = dashscope_credentials()
        return creds.enabled and bool(creds.api_key)

    async def synthesize(
        self,
        *,
        model: str,
        text: str,
        voice: str,
        rate: float,
        instruction: str,
    ) -> QwenSpeechAudio:
        from ....core.telemetry.wire_capture import set_wire_from_http_body

        creds = dashscope_credentials()
        if not creds.api_key.strip():
            raise RuntimeError("未配置 DashScope API Key,请在 Web 控制台填写 HappyHorse_apikey_dashscope")
        if not creds.enabled:
            raise RuntimeError("DashScope 未启用:请在 Web 控制台打开 HappyHorse_Enable_dashscope")
        base = creds.base_url or DASHSCOPE_DEFAULT_BASE_URL
        url = dashscope_tts_ws_url(base)
        spoken_rate = _clamp_rate(rate)
        style = instruction.strip()
        set_wire_from_http_body(
            qwen_tts_wire_body(model=model, text=text, voice=voice, rate=spoken_rate, instruction=style),
            prompt=text,
        )
        task_id = str(uuid.uuid4())
        run = _run_command(task_id=task_id, model=model, voice=voice, rate=spoken_rate, instruction=style)
        opener = self._connect or _open_socket
        logger.info(f"[DashScope-Speech] model={model} voice={voice} instruction={'yes' if style else 'no'}")
        sock = await opener(url, creds.api_key)
        try:
            return await asyncio.wait_for(_exchange(sock, task_id, run, text), timeout=_TIMEOUT_S)
        except TimeoutError as exc:
            raise RuntimeError("DashScope 语音超时") from exc
        finally:
            await sock.close()


__all__ = [
    "DashScopeSpeechAPI",
    "QwenConnect",
    "QwenSpeechAudio",
    "QwenSpeechSocket",
    "QwenTtsWire",
    "dashscope_tts_ws_url",
    "qwen_tts_wire_body",
]
