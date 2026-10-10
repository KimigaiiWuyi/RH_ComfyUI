"""Gemini 生图回包 → 分项 token,供 banana2 / banana2.1 后结算。

预扣仍按输出分辨率估算。成功后按厂商实扣:
输入(文本/图片同价)、图片输出、文本输出与思考 token 各用自己的单价。
原生回包读 usage_metadata。聚合网关读 usage.rawUsage 的
input_tokens / output_tokens;没有输出明细时,输出 token 全部按图片计。
分项加权后只向上取整一次。解析不到 usage 时返回 None,维持预扣。
"""

from __future__ import annotations

from typing import TypedDict


class GeminiImageTokenParts(TypedDict):
    input_tokens: int
    image_output_tokens: int
    text_output_tokens: int
    thoughts_tokens: int
    total_tokens: int


def _as_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            out[key] = item
    return out


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    number = int(value)
    return number if number > 0 else 0


def _field_int(blob: dict[str, object], key: str) -> int:
    if key not in blob:
        return 0
    return _positive_int(blob[key])


_NEST_KEYS: tuple[str, ...] = (
    "usage_metadata",
    "rawUsage",
    "raw_usage",
    "usage",
    "raw_task",
    "raw",
    "result",
)
_GATEWAY_TOKEN_KEYS: tuple[str, ...] = (
    "input_tokens",
    "output_tokens",
    "prompt_tokens",
    "input_tokens_details",
    "output_tokens_details",
)


def find_usage_metadata(payload: object) -> dict[str, object] | None:
    """从 NodeOutput.usage、raw 或 raw_task 里取出 usage_metadata。"""
    seen: set[int] = set()

    def walk(node: object) -> dict[str, object] | None:
        if not isinstance(node, dict):
            return None
        oid = id(node)
        if oid in seen:
            return None
        seen.add(oid)
        blob = _as_dict(node)
        if blob is None:
            return None
        if "usage_metadata" in blob:
            meta = _as_dict(blob["usage_metadata"])
            if meta:
                return meta
        for key in _NEST_KEYS:
            if key not in blob or key == "usage_metadata":
                continue
            found = walk(blob[key])
            if found is not None:
                return found
        if "prompt_token_count" in blob or "candidates_token_count" in blob or "total_token_count" in blob:
            return blob
        return None

    return walk(payload)


def _is_gateway_token_blob(blob: dict[str, object]) -> bool:
    return any(key in blob for key in _GATEWAY_TOKEN_KEYS)


def _gateway_score(blob: dict[str, object]) -> int:
    score = 0
    if "output_tokens_details" in blob:
        score += 8
    if "input_tokens_details" in blob:
        score += 4
    if "output_tokens" in blob:
        score += 4
    if "input_tokens" in blob:
        score += 2
    if "prompt_tokens" in blob:
        score += 1
    return score


def find_gateway_token_blob(payload: object) -> dict[str, object] | None:
    """从任务回包里取出聚合网关的 token 对象。只有 total_tokens 不算。"""
    best: dict[str, object] | None = None
    best_score = 0
    stack: list[tuple[object, int]] = [(payload, 0)]
    seen: set[int] = set()
    while stack:
        cur, depth = stack.pop()
        if not isinstance(cur, dict):
            continue
        oid = id(cur)
        if oid in seen:
            continue
        seen.add(oid)
        blob = _as_dict(cur)
        if blob is None:
            continue
        if _is_gateway_token_blob(blob):
            score = _gateway_score(blob) * 10 + depth
            if score > best_score:
                best = blob
                best_score = score
        for key in _NEST_KEYS:
            if key in blob:
                stack.append((blob[key], depth + 1))
    return best


def _modality_tokens(details: object, modality: str) -> int | None:
    """details 缺失返回 None;存在则只加总指定模态(没有该模态就是 0)。"""
    if not isinstance(details, list) or not details:
        return None
    total = 0
    wanted = modality.upper()
    for item in details:
        row = _as_dict(item)
        if row is None or "modality" not in row:
            continue
        name = row["modality"]
        if isinstance(name, str) and name.upper() == wanted:
            total += _field_int(row, "token_count")
    return total


def _parts_from_gemini_meta(meta: dict[str, object]) -> GeminiImageTokenParts | None:
    prompt = _field_int(meta, "prompt_token_count")
    candidates = _field_int(meta, "candidates_token_count")
    thoughts = _field_int(meta, "thoughts_token_count")
    if prompt + candidates + thoughts <= 0:
        return None
    details = meta["candidates_tokens_details"] if "candidates_tokens_details" in meta else None
    image_out = _modality_tokens(details, "IMAGE")
    if image_out is None:
        image_output = candidates
        text_output = 0
    else:
        image_output = min(image_out, candidates)
        text_output = candidates - image_output
    total = _field_int(meta, "total_token_count")
    if total <= 0:
        total = prompt + candidates + thoughts
    return GeminiImageTokenParts(
        input_tokens=prompt,
        image_output_tokens=image_output,
        text_output_tokens=text_output,
        thoughts_tokens=thoughts,
        total_tokens=total,
    )


def _parts_from_gateway_blob(blob: dict[str, object]) -> GeminiImageTokenParts | None:
    """input/output token。无输出明细时,输出全部按图片。"""
    input_tokens = _field_int(blob, "input_tokens")
    if input_tokens <= 0:
        input_tokens = _field_int(blob, "prompt_tokens")
    output_tokens = _field_int(blob, "output_tokens")
    thoughts = _field_int(blob, "thoughts_token_count")
    details = _as_dict(blob["output_tokens_details"]) if "output_tokens_details" in blob else None
    if details is None:
        image_output = output_tokens
        text_output = 0
    else:
        if thoughts <= 0:
            thoughts = _field_int(details, "reasoning_tokens")
        image_output = _field_int(details, "image_tokens")
        text_output = _field_int(details, "text_tokens")
        if image_output == 0 and text_output == 0:
            image_output = output_tokens
        elif thoughts > 0 and output_tokens > 0 and image_output + text_output + thoughts > output_tokens:
            if text_output >= thoughts:
                text_output -= thoughts
            elif image_output >= thoughts:
                image_output -= thoughts
    if input_tokens + image_output + text_output + thoughts <= 0:
        return None
    total = _field_int(blob, "total_tokens")
    if total <= 0:
        total = input_tokens + image_output + text_output + thoughts
    return GeminiImageTokenParts(
        input_tokens=input_tokens,
        image_output_tokens=image_output,
        text_output_tokens=text_output,
        thoughts_tokens=thoughts,
        total_tokens=total,
    )


def parse_gemini_image_usage(payload: object) -> GeminiImageTokenParts | None:
    """拆出计费四项。有 usage_metadata 时优先于网关 rawUsage。"""
    meta = find_usage_metadata(payload)
    if meta is not None:
        return _parts_from_gemini_meta(meta)
    blob = find_gateway_token_blob(payload)
    if blob is None:
        return None
    return _parts_from_gateway_blob(blob)


def settle_gemini_image_points(
    payload: object,
    *,
    input_points_per_million: int,
    text_points_per_million: int,
    image_points_per_million: int,
) -> int | None:
    """分项 token × 积分/1M,总和向上取整。无法解析时 None。"""
    parts = parse_gemini_image_usage(payload)
    if parts is None:
        return None
    weighted = (
        parts["input_tokens"] * input_points_per_million
        + (parts["text_output_tokens"] + parts["thoughts_tokens"]) * text_points_per_million
        + parts["image_output_tokens"] * image_points_per_million
    )
    if weighted <= 0:
        return None
    return (weighted + 999_999) // 1_000_000


def usage_from_gemini_raw(raw: object) -> dict[str, object]:
    """挂到 NodeOutput.usage。两种用量都没有时返回空 dict。"""
    parts = parse_gemini_image_usage(raw)
    if parts is None:
        return {}
    out: dict[str, object] = {
        "vendor_unit": "tokens",
        "input_tokens": parts["input_tokens"],
        "output_tokens": parts["image_output_tokens"] + parts["text_output_tokens"],
        "thoughts_token_count": parts["thoughts_tokens"],
        "total_tokens": parts["total_tokens"],
    }
    meta = find_usage_metadata(raw)
    if meta is not None:
        out["usage_metadata"] = dict(meta)
        return out
    blob = find_gateway_token_blob(raw)
    if blob is not None:
        out["raw_usage"] = dict(blob)
    return out


__all__ = [
    "GeminiImageTokenParts",
    "find_gateway_token_blob",
    "find_usage_metadata",
    "parse_gemini_image_usage",
    "settle_gemini_image_points",
    "usage_from_gemini_raw",
]
