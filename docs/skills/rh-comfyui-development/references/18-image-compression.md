# 十八、图片像素量压缩（上传/传输前瘦身）

> 模块位置：`RH_ComfyUI/utils/image_process.py`
> 公开 API：`compress_to_max_pixels` / `compress_to_max_pixels_async` / `DEFAULT_MAX_PIXELS`

## 背景

多处场景需要在上传/传输前对图片做尺寸压缩，避免 4K 原图直传：

| 调用方 | 场景 | 说明 |
|--------|------|------|
| `canvas_backend` → `canvas_backend_r2.publish()` | R2 上传前 | 上游基座不需要 4K 图，白烧带宽和存储 |
| `aigc_system` → `seedance_gateway` Seedream 5.0 通道 | data URI 内联前 | 压缩后 data URI 不触发网关 413 |

为避免各插件各写一份，压缩逻辑统一收敛到 `RH_ComfyUI.utils.image_process`。

## API

### `compress_to_max_pixels(data, mime, *, max_pixels, jpeg_quality, webp_quality) -> (bytes, str)`

同步版。按像素量（宽×高）等比压缩图片，**保持原格式不变**。

- **阈值**：`max_pixels` 默认 `DEFAULT_MAX_PIXELS = 1920 * 1080`（≈207 万像素）
- **行为**：
  - `宽×高 > max_pixels` → 等比缩小到该范围内（LANCZOS 重采样）
  - `宽×高 ≤ max_pixels` → 原样返回，**绝不放大**（480P/720P/1080P 不动）
- **格式保持**：PNG→PNG（optimize）、JPEG→JPEG（quality=85）、WebP→WebP（quality=85）
- **安全降级**：任何异常（PIL 未装、解码失败、编码失败、压缩后反而更大）→ 静默返回原始 data
- **返回值**：`(compressed_bytes, info)` — info 是人类可读的压缩描述（空串 = 未压缩），可直接拼日志

### `compress_to_max_pixels_async(...)` 

异步包装：PIL 是 CPU 密集阻塞操作，丢进 `asyncio.to_thread` 线程池，不阻塞事件循环。签名和返回值与同步版完全一致。非图片 mime 直接短路返回（不进线程池）。

### `DEFAULT_MAX_PIXELS`

常量 `1920 * 1080`。调用方可传自定义 `max_pixels` 覆盖。

## 使用示例

```python
from RH_ComfyUI.utils.image_process import compress_to_max_pixels_async

# 异步上下文（推荐）
compressed, info = await compress_to_max_pixels_async(raw_bytes, "image/jpeg")
if info:
    logger.info(f"图片压缩: {info}")
# compressed 保持原格式，可直接上传/编码

# 同步上下文
from RH_ComfyUI.utils.image_process import compress_to_max_pixels

compressed, info = compress_to_max_pixels(raw_bytes, "image/png", max_pixels=1280*720)
```

## 设计约束

1. **只处理图片 mime**（`image/png`、`image/jpeg`、`image/webp`）；视频/音频/其他类型直接透传。
2. **惰性导入 PIL**：函数体内 `from PIL import Image`，模块加载不依赖 Pillow。
3. **不改变格式**：输入 PNG 输出 PNG，输入 JPEG 输出 JPEG。不做格式转换。
4. **不放大**：只缩不放。小图（480P 等）原样保留。
5. **压缩后更大则放弃**：极小概率场景（已高度压缩的源），返回原始 data。
6. **异常不传播**：压缩是优化，不能让它炸掉上传/生成主路。

## 调用方接入清单

| 插件 | 文件 | 接入方式 |
|------|------|----------|
| canvas_backend | `canvas_backend_r2/__init__.py` | `publish()` 内调用 `compress_to_max_pixels_async` |
| aigc_system | `seedance_gateway/multimodal.py` | `GatewaySeedreamChannel._materialize_images()` 覆写中调用 |

新增调用方只需 `from RH_ComfyUI.utils.image_process import compress_to_max_pixels_async`，无需重复实现。
