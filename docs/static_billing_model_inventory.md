# 静态积分模型清单（尚未接入动态计费）

> 更新日期:2026-07-21

## 背景

当前以下模型已接入动态积分计费:
- `gpt-image-2`:按 quality + ratio + image_size 折算 tokens,210 元/1M tokens
- `banana_pro`:独立计费,输入 0.0011 美元/张 + 输出 120 美元/1M tokens 按分辨率分档
- `banana2`:按输出分辨率分档,60 美元/1M tokens
- `banana1`:固定 1290 tokens,30 美元/1M tokens
- `fish_tts` (Fish Audio S2.1-pro):按输入文本 UTF-8 字节计费,15 美元/M bytes
- `IndexTTS2`:按输入文本 UTF-8 字节计费,5 美元/M bytes
- `fish_asr` (Fish Audio ASR):按输入音频时长计费,0.36 美元/音频小时
- `ace_step1.5`:固定 10 积分
- `seedream5_pro`:输入图首张免费 + 第 2 起 2 积分/张,输出图按分辨率分档(1K=30, 2K=60)
- `seedance2`:按 token 用量计费(分分辨率 + 有无输入视频)
- `seedance2_fast`:按 token 用量计费(37 元/M 无输入,22 元/M 有输入)
- `seedance2_mini`:按 token 用量计费(23 元/M 无输入,14 元/M 有输入)
- `seedance15_pro`:按 token 用量计费(有声 16 元/M,无声 8 元/M)
- `wan2.2_videogen`:按输出时长计费,0.6 元/秒 = 60 积分/秒
- `mimo_tts` (MiMo-V2-TTS):按 UTF-8 字节计费,6 美元/M bytes → 600 积分/M bytes
- `minimax_t2a_speech` (speech-2.8-hd):按字符数计费,3.5 元/万字符 = 350 积分/万字符

以下模型已确定固定积分(不随参数变化):
- `anima`:固定 2 积分
- `minimax_image01`:固定 3 积分
- `qwen_2511`:固定 15 积分
- `qwen_2512`:固定 15 积分
- `seedream5`:固定 22 积分(0.22 元/张)

**以下所有模型仍使用原始固定 `point_cost`,尚未调整。**

---

## 一、RH_ComfyUI 插件

### 1.1 图片模型 (`RH_ComfyUI/models/image/defs.py`)

> `anima`、`minimax_image01`、`qwen_2511`、`qwen_2512`、`seedream5` 已确定固定积分,见上文"已确定固定积分"列表。

| 模型 ID | 展示名 | point_cost | 类名 | 计费方式 |
|---------|--------|:----------:|------|----------|
| `minimax_image01` | MiniMax Image-01 | 3 | `MinimaxImage01Def` | 固定 3 积分 |
| `qwen_2511` | Qwen-Edit 2511 | 15 | `Qwen2511Def` | 固定 15 积分 |
| `qwen_2512` | Qwen-Image 2512 | 15 | `Qwen2512Def` | 固定 15 积分 |
| `seedream5` | Seedream 5.0 Lite | 22 | `Seedream5Def` | 固定 22 积分 |

**文件路径**: `E:/AIProject/gsuid_core/gsuid_core/plugins/RH_ComfyUI/RH_ComfyUI/models/image/defs.py`

---

### 1.2 视频模型 (`RH_ComfyUI/models/video/defs.py`)

| 模型 ID | 展示名 | point_cost | 类名 | 计费方式 |
|---------|--------|:----------:|------|----------|
| `seedance15_pro` | Seedance 1.5 Pro | 18 | `Seedance15ProDef` | **动态** (token 用量,有声 16 元/M,无声 8 元/M) |
| `seedance2` | Seedance 2.0 | 20 | `Seedance2Def` | **动态** (token 用量,分分辨率 + 有无输入视频) |
| `seedance2_mini` | Seedance 2.0 Mini | 10 | `Seedance2MiniDef` | **动态** (token 用量,23 元/M 无输入,14 元/M 有输入) |
| `seedance2_fast` | Seedance 2.0 Fast | 15 | `Seedance2FastDef` | **动态** (token 用量,37 元/M 无输入,22 元/M 有输入) |
| `wan2.2_videogen` | Wan 2.2 VideoGen | 15 | `Wan22VideogenDef` | **动态** (时长,0.6 元/秒 = 60 积分/秒) |

**文件路径**: `E:/AIProject/gsuid_core/gsuid_core/plugins/RH_ComfyUI/RH_ComfyUI/models/video/defs.py`

---

### 1.3 音乐模型 (`RH_ComfyUI/models/music/defs.py`)

| 模型 ID | 展示名 | point_cost | 类名 | 计费方式 |
|---------|--------|:----------:|------|----------|
| `ace_step1.5` | ACE Step 1.5 | 10 | `AceStep15Def` | 固定 10 积分 |

**文件路径**: `E:/AIProject/gsuid_core/gsuid_core/plugins/RH_ComfyUI/RH_ComfyUI/models/music/defs.py`

---

### 1.4 语音模型 (`RH_ComfyUI/models/speech/defs.py`)

| 模型 ID | 展示名 | point_cost | 类名 | 计费方式 |
|---------|--------|:----------:|------|----------|
| `IndexTTS2` | IndexTTS 2 | 2 | `IndexTTS2Def` | **动态** (UTF-8 字节,5 美元/M bytes) |
| `mimo_tts` | MiMo TTS | 3 | `MimoTtsDef` | **动态** (UTF-8 字节,6 美元/M bytes) |
| `minimax_t2a_speech` | MiniMax T2A Speech | 3 | `MinimaxT2aSpeechDef` | **动态** (字符数,3.5 元/万字符) |
| `fish_tts` | Fish TTS | 2 | `FishTtsDef` | **动态** (UTF-8 字节,15 美元/M bytes) |

**文件路径**: `E:/AIProject/gsuid_core/gsuid_core/plugins/RH_ComfyUI/RH_ComfyUI/models/speech/defs.py`

---

### 1.5 ASR 模型 (`RH_ComfyUI/models/asr/defs.py`)

| 模型 ID | 展示名 | point_cost | 类名 | 计费方式 |
|---------|--------|:----------:|------|----------|
| `fish_asr` | Fish ASR | 2 | `FishAsrDef` | **动态** (音频时长,0.36 美元/音频小时) |

**文件路径**: `E:/AIProject/gsuid_core/gsuid_core/plugins/RH_ComfyUI/RH_ComfyUI/models/asr/defs.py`

---

## 二、aigc_system 插件 — AI 基座

### 2.1 独有模型 (`aigc_system/aigc_system/aifoundation/models.py`)

| 模型 ID | 展示名 | point_cost | 构建函数 | 计费方式 |
|---------|--------|:----------:|----------|----------|
| `seedance10_pro_fast` | Seedance 1.0 Pro Fast | 10 | `build_seedance10_fast_model()` | 静态 |
| `kling_v3` | Kling V3 | 15 | `build_kling_v3_model()` | 静态 |
| `mureka` | Mureka | 8 | `build_mureka_model()` | 静态 |

**文件路径**: `E:/AIProject/gsuid_core/gsuid_core/plugins/aigc_system/aigc_system/aifoundation/models.py`

---

### 2.2 聚合网关海外版 (`aigc_system/aigc_system/seedance_gateway/models.py`)

| 模型 ID | 展示名 | point_cost | 来源 | 计费方式 |
|---------|--------|:----------:|------|----------|
| `seedance2_intl` | Seedance 2.0 海外版 | 20 | `INTL_MODELS` 元组 | 静态 |
| `seedance2_intl_mini` | Seedance 2.0 Mini 海外版 | 10 | `INTL_MODELS` 元组 | 静态 |
| `seedance2_intl_fast` | Seedance 2.0 Fast 海外版 | 15 | `INTL_MODELS` 元组 | 静态 |

**文件路径**: `E:/AIProject/gsuid_core/gsuid_core/plugins/aigc_system/aigc_system/seedance_gateway/models.py`

---

## 三、已接入动态计费的模型

| 模型 ID | 展示名 | 文件路径 | 计费方式 |
|---------|--------|----------|----------|
| `gpt-image-2` | GPT-Image2 | `RH_ComfyUI/models/image/defs.py` (`GptImage2Def`) | **动态** (quality + ratio + image_size → tokens,210 元/1M) |
| `banana_pro` | Nano Banana Pro | `RH_ComfyUI/models/image/defs.py` (`BananaProDef`) | **动态** (输入 0.0011 美元/张 + 输出 120 美元/1M tokens 分档) |
| `banana2` | Nano Banana 2 | `RH_ComfyUI/models/image/defs.py` (`Banana2Def`) | **动态** (按输出分辨率分档,60 美元/1M tokens) |
| `banana1` | Nano Banana 1 | `RH_ComfyUI/models/image/defs.py` (`Banana1Def`) | **动态** (固定 1290 tokens,30 美元/1M tokens) |
| `seedream5_pro` | Seedream 5.0 Pro | `RH_ComfyUI/models/image/defs.py` (`Seedream5ProDef`) | **动态** (输入图首张免费 + 第 2 起 2 积分/张,输出图按分辨率分档) |
| `fish_tts` | Fish Audio S2 | `RH_ComfyUI/models/speech/defs.py` (`FishTtsDef`) | **动态** (UTF-8 字节,15 美元/M bytes) |
| `IndexTTS2` | IndexTTS2 | `RH_ComfyUI/models/speech/defs.py` (`IndexTTS2Def`) | **动态** (UTF-8 字节,5 美元/M bytes) |
| `fish_asr` | Fish Audio ASR | `RH_ComfyUI/models/asr/defs.py` (`FishAsrDef`) | **动态** (音频时长,0.36 美元/音频小时) |
| `ace_step1.5` | ACE Step 1.5 | `RH_ComfyUI/models/music/defs.py` (`AceStep15Def`) | **固定** 10 积分 |
| `seedance2` | Seedance 2.0 | `RH_ComfyUI/models/video/defs.py` (`Seedance2Def`) | **动态** (token 用量,分分辨率 + 有无输入视频) |
| `seedance2_fast` | Seedance 2.0 Fast | `RH_ComfyUI/models/video/defs.py` (`Seedance2FastDef`) | **动态** (token 用量,37 元/M 无输入,22 元/M 有输入) |
| `seedance2_mini` | Seedance 2.0 Mini | `RH_ComfyUI/models/video/defs.py` (`Seedance2MiniDef`) | **动态** (token 用量,23 元/M 无输入,14 元/M 有输入) |
| `seedance15_pro` | Seedance 1.5 Pro | `RH_ComfyUI/models/video/defs.py` (`Seedance15ProDef`) | **动态** (token 用量,有声 16 元/M,无声 8 元/M) |
| `wan2.2_videogen` | Wan 2.2 VideoGen | `RH_ComfyUI/models/video/defs.py` (`Wan22VideogenDef`) | **动态** (时长,0.6 元/秒 = 60 积分/秒) |
| `mimo_tts` | MiMo TTS | `RH_ComfyUI/models/speech/defs.py` (`MimoTtsDef`) | **动态** (UTF-8 字节,6 美元/M bytes) |
| `minimax_t2a_speech` | MiniMax T2A Speech | `RH_ComfyUI/models/speech/defs.py` (`MinimaxT2aSpeechDef`) | **动态** (字符数,3.5 元/万字符) |

---

## 四、统计汇总

| 插件 | 静态模型数 | 动态/固定模型数 | 合计 |
|------|:---------:|:---------:|:----:|
| RH_ComfyUI (图片) | 0 | 10 | 10 |
| RH_ComfyUI (视频) | 0 | 5 | 5 |
| RH_ComfyUI (音乐) | 0 | 1 | 1 |
| RH_ComfyUI (语音) | 0 | 4 | 4 |
| RH_ComfyUI (ASR) | 0 | 1 | 1 |
| aigc_system (aifoundation) | 3 | 0 | 3 |
| aigc_system (seedance_gateway) | 3 | 0 | 3 |
| **合计** | **6** | **21** | **27** |

---

## 五、接入动态计费的通用模式

如需为上述模型新增动态计费,标准做法:

1. **在模型类中覆盖 `estimate_cost()` 方法**:

```python
# RH_ComfyUI/models/image/defs.py
class AnimaDef(ImagePipelineModel):
    # ... node_def() ...

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:根据请求参数计算积分"""
        # 从 request.params 读取用户选择的参数
        some_param = request.params.get("some_param")
        # 计算并返回积分
        return calculated_points
```

2. **如需跨字段校验,同时覆盖 `validate()`**:

```python
    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)  # 先跑 schema 通用校验
        # 补跨字段约束
        if some_condition:
            raise ValidationError("...")
```

3. **在 `utils/mappers/` 下创建计费工具函数**(复杂模型)

4. **在 `tests/` 下新增测试覆盖 `estimate_cost()`**

---

## 六、参考实现

动态计费的完整参考实现:

- **计费工具**:
  - `RH_ComfyUI/utils/mappers/gpt_image2_billing.py` — GPT-Image-2 (210 元/1M tokens)
  - `RH_ComfyUI/utils/mappers/banana_pro_billing.py` — Nano Banana Pro (独立计费)
  - `RH_ComfyUI/utils/mappers/nanobanana2_billing.py` — Nano Banana 2 (60 美元/1M tokens)
  - `RH_ComfyUI/utils/mappers/nanobanana1_billing.py` — Nano Banana 1 (30 美元/1M tokens)
  - `RH_ComfyUI/utils/mappers/seedream5_pro_billing.py` — Seedream 5.0 Pro (输入图+输出像素)
  - `RH_ComfyUI/utils/mappers/speech_billing.py` — Fish TTS / IndexTTS2 (UTF-8 字节)
  - `RH_ComfyUI/utils/mappers/fishaudio_asr_billing.py` — Fish Audio ASR (音频时长)
  - `RH_ComfyUI/utils/mappers/seedance_billing.py` — Seedance 2.0/1.5/1.0 (token 用量)
  - `RH_ComfyUI/utils/mappers/extra_billing.py` — Wan2.2 / MiMo TTS / MiniMax T2A
- **模型覆盖**:
  - 图片: `GptImage2Def`、`BananaProDef`、`Banana2Def`、`Banana1Def`、`Seedream5ProDef`
  - 语音: `FishTtsDef`、`IndexTTS2Def`、`MimoTtsDef`、`MinimaxT2aSpeechDef`
  - ASR: `FishAsrDef`
  - 音乐: `AceStep15Def` (固定 10 积分)
  - 视频: `Seedance2Def`、`Seedance2FastDef`、`Seedance2MiniDef`、`Seedance15ProDef`、`Wan22VideogenDef`
- **估算接口**: `RH_ComfyUI/rh_models/api.py` 中的 `estimate_model_points()`
- **前端对接**: `RH_ComfyUI/docs/frontend_dynamic_billing.md`
- **测试用例**: `RH_ComfyUI/tests/test_gpt_image2_billing.py`、`RH_ComfyUI/tests/test_nanobanana_billing.py`、`RH_ComfyUI/tests/test_new_billing_modules.py`、`RH_ComfyUI/tests/test_seedance_billing.py`、`RH_ComfyUI/tests/test_extra_billing.py`、`RH_ComfyUI/tests/test_estimate_endpoint.py`

---

## 七、变更日志

### 2026-07-21 (续四)

- `wan2.2_videogen` 接入动态计费:按输出时长计费,0.6 元/秒 = 60 积分/秒
- `mimo_tts` (MiMo-V2-TTS) 接入动态计费:按 UTF-8 字节计费,6 美元/M bytes → 600 积分/M bytes
- `minimax_t2a_speech` (speech-2.8-hd) 接入动态计费:按字符数计费,3.5 元/万字符 = 350 积分/万字符
- 新增计费工具模块: `extra_billing.py`
- 新增测试: `tests/test_extra_billing.py` (18 个用例)

### 2026-07-21 (续三)

- `seedance2` 接入动态计费:按 token 用量计费,分分辨率(480p/720p/1080p/4K)和有无输入视频
- `seedance2_fast` 接入动态计费:按 token 用量计费(37 元/M 无输入,22 元/M 有输入)
- `seedance2_mini` 接入动态计费:按 token 用量计费(23 元/M 无输入,14 元/M 有输入)
- `seedance15_pro` 接入动态计费:按 token 用量计费(有声 16 元/M,无声 8 元/M)
- token 用量公式:(输入视频时长 + 输出视频时长) × 输出视频宽 × 输出视频高 × 输出视频帧率 / 1024
- 新增计费工具模块: `seedance_billing.py`
- 新增测试: `tests/test_seedance_billing.py` (33 个用例)

### 2026-07-21 (续二)

- `seedream5_pro` 接入动态计费:输入图首张免费 + 第 2 张起 2 积分/张,输出图按分辨率分档(1K=30, 2K=60)
- `fish_tts` (S2.1-pro) 接入动态计费:按输入文本 UTF-8 字节计费,15 美元/M bytes → 1500 积分/M bytes
- `IndexTTS2` 接入动态计费:按输入文本 UTF-8 字节计费,5 美元/M bytes → 500 积分/M bytes
- `fish_asr` 接入动态计费:按输入音频时长计费,0.36 美元/音频小时 → 36 积分/音频小时
- `ace_step1.5` 调整为固定 10 积分
- `seedream5` 调整为固定 22 积分(0.22 元/张)
- `qwen_2511` 调整为固定 15 积分
- `qwen_2512` 调整为固定 15 积分
- `minimax_image01` 保持固定 3 积分
- 新增计费工具模块: `seedream5_pro_billing.py`、`speech_billing.py`、`fishaudio_asr_billing.py`
- 新增测试: `tests/test_new_billing_modules.py` (42 个用例)

### 2026-07-21 (续一)

- `banana2` 接入动态计费:按输出分辨率分档(512/1K/2K/4K),60 美元/1M tokens
- `banana1` 接入动态计费:固定 1290 tokens,30 美元/1M tokens
- `banana_pro` 从共享 gpt-image-2 计费改为独立计费:输入 0.0011 美元/张 + 输出 120 美元/1M tokens 按分辨率分档
- `anima` 保持静态,恒为 2 积分
- 新增计费工具模块: `nanobanana2_billing.py`、`nanobanana1_billing.py`、`banana_pro_billing.py`
- 新增测试: `tests/test_nanobanana_billing.py` (43 个用例)

### 2026-07-21

- `gpt-image-2` 和 `banana_pro` 接入动态计费
- 新增 `GET /api/RH_ComfyUI/models/estimate` 接口
- 其余 25 个模型保持静态 `point_cost`
