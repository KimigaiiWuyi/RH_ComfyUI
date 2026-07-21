# 前端积分范围展示对接文档

> 更新日期:2026-07-21
> 适用版本:gsuid_core ≥ 2026.07.21

## 一、背景

为了让用户在模型选择阶段就能直观了解每次生成的积分消耗范围,后端新增了两类接口字段:

1. **模型清单接口** `GET /api/RH_ComfyUI/models` — 每个模型附带 `point_range` 字段
2. **实时估算接口** `GET /api/RH_ComfyUI/models/estimate` — 根据用户当前选择的参数实时估算积分

---

## 二、模型清单接口

### 请求

```
GET http://localhost:8765/api/RH_ComfyUI/models
GET http://localhost:8765/api/RH_ComfyUI/models?task_type=image
GET http://localhost:8765/api/RH_ComfyUI/models?task_type=video
```

### 响应变动

每个模型条目新增 `point_range` 字段:

```json
{
  "models": [
    {
      "name": "seedance2",
      "display_name": "Seedance 2.0",
      "point_cost": 20,
      "point_range": {"min": 55, "max": 5177},
      "...": "..."
    },
    {
      "name": "anima",
      "display_name": "Anima",
      "point_cost": 2,
      "point_range": {"min": 2, "max": 2},
      "...": "..."
    },
    {
      "name": "banana2",
      "display_name": "Nano Banana 2",
      "point_cost": 2,
      "point_range": {"min": 5, "max": 16},
      "...": "..."
    }
  ]
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `point_range.min` | int | 该模型单次请求的最低积分(最便宜参数组合) |
| `point_range.max` | int | 该模型单次请求的最高积分(最贵参数组合) |

- 固定积分模型:`min === max`(如 `anima` 恒为 2 积分)
- 动态积分模型:`min < max`(如 `seedance2` 范围 55~5177)

---

## 三、实时估算接口

### 请求

```
GET http://localhost:8765/api/RH_ComfyUI/models/estimate?model=seedance2&resolution=1080p&duration=10
GET http://localhost:8765/api/RH_ComfyUI/models/estimate?model=banana2&image_size=4K
GET http://localhost:8765/api/RH_ComfyUI/models/estimate?model=gpt-image-2&quality=high&image_size=2K
```

### 参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `model` | string | 模型 ID(必填) |
| `resolution` | string | 分辨率(视频模型用,如 `720p`/`1080p`) |
| `duration` | int | 时长(秒,视频/语音模型用) |
| `image_size` | string | 尺寸档位(图片模型用,如 `1K`/`2K`/`4K`) |
| `quality` | string | 质量档位(如 `low`/`medium`/`high`) |
| `ratio` | string | 宽高比(如 `1:1`/`16:9`) |

> 提示:不同模型接受的参数不同,具体参考该模型的 `input_schema` 字段。

### 响应变动

新增 `point_range` 字段(与清单接口相同):

```json
{
  "model": "seedance2",
  "point_cost": 1234,
  "is_dynamic": true,
  "point_range": {"min": 55, "max": 5177},
  "params": {"resolution": "1080p", "duration": 10}
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `point_cost` | int | 基于当前参数的预估积分 |
| `is_dynamic` | bool | true=动态计算,false=静态兜底 |
| `point_range.min` | int | 该模型可能的最少积分 |
| `point_range.max` | int | 该模型可能的最多积分 |
| `params` | object | 实际参与计算的参数 |

---

## 四、前端展示建议

### 4.1 模型选择列表

在每个模型卡片/列表项旁边展示积分范围:

```
┌─────────────────────────────────────────────┐
│ 🌸 Anima                            2 积分  │
├─────────────────────────────────────────────┤
│ 🍌 Nano Banana 2                 5~16 积分  │
├─────────────────────────────────────────────┤
│ 🎬 Seedance 2.0                55~5177 积分 │
└─────────────────────────────────────────────┘
```

**展示规则:**

- `min === max` 时:显示固定值 `N 积分`
- `min < max` 时:显示范围 `min~max 积分`
- 范围较大时:可简化为 `约 min~max 积分`

### 4.2 参数面板实时预览

当用户切换参数(分辨率/时长/质量等)时,调用实时估算接口,动态更新显示的积分:

```javascript
// 示例 Vue/React
async function updateEstimate(modelId, params) {
  const resp = await fetch(`/api/RH_ComfyUI/models/estimate?model=${modelId}&${new URLSearchParams(params)}`);
  const data = await resp.json();
  displayEstimate(data.point_cost);       // 当前预估
  displayRange(data.point_range);          // 范围(可忽略或灰色显示)
}
```

### 4.3 生成按钮

生成按钮旁可展示当前预估积分,帮助用户确认是否值得生成:

```
[ 🎬 生成视频 (预估 1234 积分) ]
```

---

## 五、各模型积分范围参考表

### 图片模型

| 模型 | 计费方式 | point_range.min | point_range.max |
|------|----------|:---------------:|:---------------:|
| `anima` | 固定 | 2 | 2 |
| `minimax_image01` | 固定 | 3 | 3 |
| `qwen_2511` | 固定 | 15 | 15 |
| `qwen_2512` | 固定 | 15 | 15 |
| `seedream5` | 固定 | 22 | 22 |
| `banana1` | 固定(动态计费) | 4 | 4 |
| `banana2` | 按分辨率 | 5 | 16 |
| `banana_pro` | 输入+输出 | 14 | 27 |
| `gpt-image-2` | quality+尺寸 | 17 | 245 |
| `seedream5_pro` | 输入图+输出 | 30 | 78 |

### 视频模型

| 模型 | 计费方式 | point_range.min | point_range.max |
|------|----------|:---------------:|:---------------:|
| `ace_step1.5` | 固定 | 10 | 10 |
| `wan2.2_videogen` | 按时长 | 60 | 900 |
| `seedance2_fast` | token 用量 | 44 | 1101 |
| `seedance2_mini` | token 用量 | 26 | 661 |
| `seedance15_pro` | 有声/无声 | 78 | 622 |
| `seedance2` | 分辨率+输入 | 55 | 5177 |

### 语音模型

| 模型 | 计费方式 | point_range.min | point_range.max |
|------|----------|:---------------:|:---------------:|
| `IndexTTS2` | UTF-8 字节 | 1 | 1 |
| `mimo_tts` | UTF-8 字节 | 1 | 2 |
| `minimax_t2a_speech` | 字符数 | 1 | 105 |
| `fish_tts` | UTF-8 字节 | 1 | 5 |

### ASR 模型

| 模型 | 计费方式 | point_range.min | point_range.max |
|------|----------|:---------------:|:---------------:|
| `fish_asr` | 音频时长 | 1 | 2160 |

### aigc_system 插件模型

| 模型 | 计费方式 | point_range.min | point_range.max |
|------|----------|:---------------:|:---------------:|
| `seedance10_pro_fast` | token 用量 | 自动计算 | 自动计算 |
| `kling_v3` | 分辨率+有声 | 18 | 4500 |
| `mureka` | 固定 | 5 | 5 |
| `seedance2_intl` | 国内版×1.1 | 自动计算 | 自动计算 |
| `seedance2_intl_mini` | 国内版×1.1 | 自动计算 | 自动计算 |
| `seedance2_intl_fast` | 国内版×1.1 | 自动计算 | 自动计算 |

---

## 六、注意事项

1. **向后兼容**:`point_range` 是新增字段,旧版后端可能不返回。前端应做兜底:
   ```javascript
   const range = data.point_range || {min: data.point_cost, max: data.point_cost};
   ```

2. **动态估算接口参数**:不同模型接受的参数名称不同,详见该模型的 `input_schema`。

3. **积分 ≠ 实际扣费**:`point_cost` 是预估值,实际扣费以 dispatcher 调用 `estimate_cost()` 为准(两者算法一致)。

4. **范围计算逻辑**:`point_range` 是该模型所有合法参数组合中积分的最小值和最大值,不代表用户当前选择的范围。

---

## 七、变更日志

### 2026-07-21
- `ModelEntry` 新增 `point_range: {min: int, max: int}` 字段
- `GET /api/RH_ComfyUI/models` 返回的每个模型附带 `point_range`
- `GET /api/RH_ComfyUI/models/estimate` 响应新增 `point_range`
- `AIGCGenerationBase` 新增 `point_range() -> (min, max)` 方法
- 所有动态计费模型已覆盖 `point_range()` 实现
