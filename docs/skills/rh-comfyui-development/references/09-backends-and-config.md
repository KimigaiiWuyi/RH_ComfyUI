# 九、后端(utils/backends)、映射器(utils/mappers)与配置体系

## 9.1 后端 Adapter 层

`utils/backends/` 是所有上游通信细节的家。每个后端是一个 `Adapter` 子类
(`base.py`),注册进 `backend_registry`(`init_backends()` 启动时调用)。

| 后端名(backend 字段) | 目录 | 上游 | 说明 |
|---|---|---|---|
| `comfyui` | `comfyui/` | 本地/远程 ComfyUI | WebSocket + workflow JSON;`set_workflow_override()` 支持 mapper 按输入切换工作流(如 Wan t2v/i2v) |
| `gpt-image-2` | `gpt_image2/` | OpenAI 兼容协议 | 文生图/图生图/编辑自适应;凭证可指向 OneAPI/NewAPI 等网关(注意名字带**连字符**) |
| `rh_app` | `rh_app/` | RunningHub AI 应用 | webapp id 即 `workflow_file` |
| `minimax` | `minimax/` | MiniMax | 图片 + 语音(T2A) |
| `mimo` | `mimo/` | MiMo TTS | 语音 |
| `seedance` | `seedance/` | 字节 Seedance 系列 | 内部有独立的 Provider 层(ARK/Gateway/RunningHub 三供应商)+ 负载均衡 + 熔断 + Dry-Run |

Adapter 协议(5 成员):`name` / `check_available()` / `get_unavailable_reason()` /
`capabilities()` / `execute(request, node, on_progress) -> NodeOutput`。

**分工边界**:Adapter 管通信与厂商协议;模型类(models/)管参数面与校验;
`AdapterChannel`(models/bridge.py)把 Adapter 适配为 ProviderChannel 供
ABC 执行链使用。新上游 = 新 Adapter(或直接写 ProviderChannel,见 04 章)。

### Seedance 的 Provider 子层

`backends/seedance/` 内部:`provider.py`(SeedanceProvider 细粒度抽象:
render/parse/poll + 形态/分辨率支持声明)+ `registry.py`(供应商选择)。
模型 YAML 时代的 `backend_models`(ark/gateway/runninghub → 各家模型 ID)
现在声明在 defs.py 的 `node_def()` 里;节点级 `provider` 字段可固定走某家。

## 9.2 映射器(utils/mappers)

mapper = "GenerationRequest → 厂商 payload / ComfyUI workflow" 的纯函数。
`node_def()` 里 `mode="programmatic"` + `mapper_func=xxx` 直接引用
(defs.py 顶部 `from ...utils.mappers.xxx import yyy`);
`mode="declarative"` + `mappings=[{source, target}]` 用于简单的
ComfyUI 字段注入。

| 文件 | 服务的模型 |
|---|---|
| `gpt_image2.py` | banana2 / banana_pro / gpt-image-2 |
| `image_edit.py` | qwen_2511(图片编辑) |
| `minimax_text2image.py` | minimax_image01 |
| `video.py` | wan2.2_videogen(含 t2v/i2v 工作流切换) |
| `music.py` | ace_step1.5 |
| `speech.py` | IndexTTS2(含参考音频注入) |
| `mimo_speech.py` / `minimax_speech.py` | mimo_tts / minimax_t2a_speech |

改某模型的请求组装 → 改对应 mapper;改参数面(校验/前端表单)→ 改 defs 的
PortSpec。两者必须同步(mapper 消费的字段要在 inputs 里声明)。

## 9.3 配置体系(rh_config/)

两个配置实例,都自动注册进 gsuid_core 网页控制台:

**SERVICE_CONFIG(service_config.py)— 上游凭证/地址**

| 键 | 用途 |
|---|---|
| `ComfyUI_BaseURL` | ComfyUI 地址 |
| `RH_apikey` | RunningHub |
| `OpenAI_Image_apikey` / `OpenAI_Image_BaseURL` | OpenAI 兼容生图 |
| `MiniMax_apikey` / `MIMO_apikey` | MiniMax / MiMo |
| `Seedance_apikey_{ark,gateway,runninghub}` + `Seedance_BaseURL_*` + `Seedance_Enable_*` | Seedance 三供应商 |
| `Seedance_Load_Balance` / `Seedance_Failure_Threshold` / `Seedance_Dry_Run` | 负载均衡策略/熔断阈值/干跑 |

**PLUGIN_CONFIG(plugin_config.py)— 插件行为**

| 键 | 用途 |
|---|---|
| `Max_Concurrency` | 全局并发闸大小 |
| `Default_Point` | 新用户初始积分 |
| `Draw_Point` / `Edit_Image_Point` / `Music_Point` / `Speech_Point` / `Video_Point` | 各任务兜底价格(模型自带 point_cost 优先) |

规则:
- 模型可用性由 `required_config` / `requirements` 引用这些键,缺失自动
  标不可用并给出人话原因;
- 新上游的配置键加在 SERVICE_CONFIG 并配 `GsDivider` 分组;
- 读配置一律 `SERVICE_CONFIG.get_config(key).data`,不要缓存到模块级
  (网页控制台改完要即时生效)。
