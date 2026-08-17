"""视频模态的编程式模型类 — Seedance 2.x / Wan 2.2 / HappyHorse(蓝图 §5.2 范本)

参数面严重不一致的模型在同一 ABC 下共存:
- Seedance 2.0:多参考(≤12 素材)/首尾帧/单图/纯文本;480p~4k;有声开关
- Seedance 2.5:多参考(≤50=图30+视频10+音频10)/编辑/延长/首尾帧;仅 480p~720p;
  时长 4~30s 或 -1;output_format mp4/mov;复用火山方舟 Key
- Wan 2.2:仅首尾帧/首帧/纯文本;≤720p(像素积约束);任意宽高比;无有声

执行链仍复用桥接层(NodeDef + Adapter),差异全部体现在能力声明与
validate() 的跨字段约束里 —— 这正是"参数处理回到代码"的落点。
"""

from __future__ import annotations

from typing import Optional

from ..bridge import VideoPipelineModel
from ...core.base.video import VideoTaskShape
from ...core.base.errors import ValidationError
from ...core.schema.card import ModelCard
from ...core.schema.types import NodeOutput, ProgressCallback
from ...core.schema.request import GenerationRequest
from ...utils.core.pipeline import NodeDef
from ...core.channels.channel import ChannelBinding
from ...core.channels.registry import channel_registry
from ...utils.backends.seedance.channel import builtin_seedance_channels
from ...utils.backends.happyhorse.channel import builtin_happyhorse_channels


class SeedanceVideoModel(VideoPipelineModel):
    """Seedance 2.0:多参考(图+视频+音频 合计≤12)/首尾帧/单图/纯文本

    火山方舟 / 网关异步任务支持 DELETE 取消(supports_remote_cancel=True)。
    """

    supports_remote_cancel = True
    # 参考视频时长:2.0 官方 2~15s;2.5 覆盖为 2~30s
    ref_video_min_s = 2.0
    ref_video_max_s = 15.0
    ref_video_loop_target_s = 2.5
    ref_video_trim_target_s = 14.5
    # 2.5 r2v 硬限 407696;2.0 同样放大无害
    ref_video_min_pixels = 407696
    # 2.0 r2v 硬限 15.2s;2.5 同样裁切
    ref_audio_max_s = 15.2
    ref_audio_trim_target_s = 15.0

    def __init__(self, node: NodeDef) -> None:
        super().__init__(node)
        self.supported_shapes = {
            VideoTaskShape.TEXT2VIDEO,
            VideoTaskShape.IMAGE2VIDEO,
            VideoTaskShape.FIRST_LAST_FRAME,
            VideoTaskShape.MULTIMODAL,
        }
        self.supports_generate_audio = True
        self.max_reference_total = 12
        self.card = ModelCard(
            description=node.description or "字节跳动 Seedance 2.0 统一视频生成,按输入自动切换全部形态",
            strengths=["中文语义理解强", "动作流畅", "多模态参考(图/视频/音频)", "可生成有声视频"],
            categories=["短视频", "写实", "多素材合成", "音画同步"],
            weaknesses=["高分辨率(4K)耗时较长且积分消耗高"],
            sample_prompts=["图片1为主角,音频1为背景音乐,他在草原上奔跑"],
            languages=["zh", "en"],
            speed_hint="slow",
        )

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)
        if len(request.video_refs) > 3:
            raise ValidationError(f"{self.display_name} 最多接受 3 段参考视频,当前 {len(request.video_refs)} 段")
        if len(request.audio_refs) > 3:
            raise ValidationError(f"{self.display_name} 最多接受 3 段参考音频,当前 {len(request.audio_refs)} 段")

    async def prepare_request(self, request: GenerationRequest) -> GenerationRequest:
        """提交前预处理:参考视频时长/像素 + 参考音频时长 + 参考图短边/宽高比。

        视频:时长钳到 [min, max];像素 < 407696 时等比放大。两步合并进一趟 ffmpeg。
        音频:超过 15.2s 裁到 15.0s。
        图片:宽或高 < 300px 时等比放大;宽高比超出 0.40~2.50 时居中裁到 0.41 或 2.49。
        多段参考并发处理,ffmpeg 由全局闸限并发。探测/编解码失败放行原片。
        """
        import asyncio

        from gsuid_core.logger import logger

        from ...core.schema.types import MediaRef, MediaKind, ContentItem, ContentItemType
        from ...utils.audio_process import clamp_seedance_ref_audio
        from ...utils.image_process import prepare_seedance_image_ref, prepare_seedance_image_bytes
        from ...utils.video_process import ensure_media_bytes, prepare_seedance_ref_video

        video_tasks: dict[str, asyncio.Task] = {}
        audio_tasks: dict[str, asyncio.Task] = {}

        def _cache_key(ref: MediaRef) -> str:
            u = (ref.url or "").strip()
            if u:
                return f"url:{u}"
            if ref.data:
                return f"data:{id(ref.data)}:{len(ref.data)}"
            return f"empty:{id(ref)}"

        def _replaced(
            ref: MediaRef,
            *,
            kind: MediaKind,
            data: bytes,
            mime: str,
        ) -> MediaRef:
            return MediaRef(
                kind=kind,
                data=data,
                url=None,
                role=ref.role,
                mime_type=mime,
                filename=ref.filename,
            )

        async def _do_video(ref: MediaRef) -> MediaRef:
            raw = await ensure_media_bytes(ref)
            if not raw:
                return ref
            new_data, _dur, action = await prepare_seedance_ref_video(
                raw,
                min_s=self.ref_video_min_s,
                max_s=self.ref_video_max_s,
                loop_target_s=self.ref_video_loop_target_s,
                trim_target_s=self.ref_video_trim_target_s,
                min_pixels=self.ref_video_min_pixels,
            )
            if action is None and new_data is raw:
                if ref.data is None and new_data:
                    return _replaced(ref, kind=MediaKind.VIDEO, data=new_data, mime=ref.mime_type or "video/mp4")
                return ref
            return _replaced(ref, kind=MediaKind.VIDEO, data=new_data, mime="video/mp4")

        async def _do_audio(ref: MediaRef) -> MediaRef:
            raw = await ensure_media_bytes(ref)
            if not raw:
                return ref
            new_data, _dur, action = await clamp_seedance_ref_audio(
                raw,
                max_s=self.ref_audio_max_s,
                trim_target_s=self.ref_audio_trim_target_s,
                mime_type=ref.mime_type or "",
            )
            if action is None and new_data is raw:
                if ref.data is None and new_data:
                    return _replaced(ref, kind=MediaKind.AUDIO, data=new_data, mime=ref.mime_type or "audio/mp4")
                return ref
            return _replaced(ref, kind=MediaKind.AUDIO, data=new_data, mime="audio/mp4")

        def _schedule_video(ref: MediaRef) -> asyncio.Task:
            key = _cache_key(ref)
            task = video_tasks.get(key)
            if task is None:
                task = asyncio.create_task(_do_video(ref))
                video_tasks[key] = task
            return task

        def _schedule_audio(ref: MediaRef) -> asyncio.Task:
            key = _cache_key(ref)
            task = audio_tasks.get(key)
            if task is None:
                task = asyncio.create_task(_do_audio(ref))
                audio_tasks[key] = task
            return task

        async def _upscale_image_ref(ref: MediaRef) -> MediaRef:
            return await prepare_seedance_image_ref(ref)

        scheduled: list[asyncio.Task] = []
        if request.video_refs:
            scheduled.extend(_schedule_video(v) for v in request.video_refs)
        if request.audio_refs:
            scheduled.extend(_schedule_audio(a) for a in request.audio_refs)
        if request.ordered_content:
            for item in request.ordered_content:
                if item.media is None:
                    continue
                if item.type == ContentItemType.VIDEO and item.media.kind == MediaKind.VIDEO:
                    scheduled.append(_schedule_video(item.media))
                elif item.type == ContentItemType.AUDIO and item.media.kind == MediaKind.AUDIO:
                    scheduled.append(_schedule_audio(item.media))
        if scheduled:
            await asyncio.gather(*scheduled)

        if request.video_refs:
            request.video_refs = [await _schedule_video(v) for v in request.video_refs]
        if request.audio_refs:
            request.audio_refs = [await _schedule_audio(a) for a in request.audio_refs]

        if request.images:
            new_images: list[bytes] = []
            for raw in request.images:
                out, info = prepare_seedance_image_bytes(raw)
                if info:
                    logger.info(f"[seedance] 参考图预处理: {info}")
                new_images.append(out)
            request.images = new_images

        if request.ordered_content:
            new_oc: list[ContentItem] = []
            for item in request.ordered_content:
                if item.media is None:
                    new_oc.append(item)
                    continue
                new_media = item.media
                if item.type == ContentItemType.VIDEO and item.media.kind == MediaKind.VIDEO:
                    new_media = await _schedule_video(item.media)
                elif item.type == ContentItemType.AUDIO and item.media.kind == MediaKind.AUDIO:
                    new_media = await _schedule_audio(item.media)
                elif item.type == ContentItemType.IMAGE and item.media.kind == MediaKind.IMAGE:
                    new_media = await _upscale_image_ref(item.media)
                new_oc.append(
                    ContentItem(
                        type=item.type,
                        media=new_media,
                        role=item.role,
                        text=item.text,
                    )
                )
            request.ordered_content = new_oc

        return request

    # ── 多供应商通道(交给通用 LoadBalancer 排序 / 熔断 / 故障切换) ──

    def _vendor_model_for(self, provider_name: str) -> Optional[str]:
        """该供应商应使用的 vendor model ID(ark 兜底 backend_model;端点即模型的供应商返回 None)。"""
        node = self.node
        vm = (node.backend_models or {}).get(provider_name) or None
        if not vm and provider_name == "ark":
            vm = node.backend_model
        return vm

    def _serves(self, provider_name: str, channel) -> bool:
        """内置供应商是否参与本节点的分发。

        - 需要 model 字段的供应商(ark):必须解析出非空 vendor model id;
        - 端点即模型的供应商(runninghub):必须在 node.backend_models 中挂名
          (值可为空串),避免"仅网关可用"的模型被错误分发到它头上。
        """
        if channel.accepts_model_field:
            return bool(self._vendor_model_for(provider_name))
        return provider_name in (self.node.backend_models or {})

    def channel_bindings(self) -> list[ChannelBinding]:
        node = self.node
        builtins = builtin_seedance_channels()
        external = channel_registry.bindings_for(node.name)

        # 节点级固定供应商 → 只走该供应商的通道
        if node.provider:
            ch = builtins.get(node.provider)
            if ch is not None:
                return [ChannelBinding(ch, vendor_model=self._vendor_model_for(node.provider))]
            return [b for b in external if b.channel.name == node.provider]

        bindings: list[ChannelBinding] = []
        for name, ch in builtins.items():
            if self._serves(name, ch):
                bindings.append(ChannelBinding(ch, vendor_model=self._vendor_model_for(name)))
        bindings.extend(external)
        return bindings

    async def execute_on_channel(
        self,
        request: GenerationRequest,
        binding: ChannelBinding,
        *,
        on_progress: Optional[ProgressCallback] = None,
    ) -> NodeOutput:
        return await binding.channel.invoke(
            request=request,
            node=self.node,
            on_progress=on_progress,
            vendor_model=binding.vendor_model,
        )

    async def unavailable_reason(self) -> str:
        return (
            f"{self.display_name} 无可用供应商:请在 Web 控制台配置 "
            "Seedance_apikey_ark / _runninghub(或安装并配置外部供应商插件)"
        )


class Seedance25VideoModel(SeedanceVideoModel):
    """Seedance 2.5:30s 连贯直出 / 50 多模态参考 / 视频编辑 / 延长 / mov 输出

    与 2.0 类型分开(独立模型名 seedance2.5 + 独立 vendor model id),
    凭证复用火山方舟 Seedance_apikey_ark。任务形态可通过 task_mode 显式指定:
      - auto: 按输入自动(文生/图生/首尾帧/多模态)
      - edit: 视频编辑(建议 ratio=adaptive, duration=-1)
      - extend: 视频延长(建议 ratio=adaptive)
    """

    MAX_IMAGES = 30
    MAX_VIDEOS = 10
    MAX_AUDIOS = 10
    MAX_REFERENCE_TOTAL = 50
    # 2.5 参考视频单段可至 30s;像素下限与 2.0 相同
    ref_video_max_s = 30.0
    ref_video_trim_target_s = 29.5

    def __init__(self, node: NodeDef) -> None:
        super().__init__(node)
        self.supported_shapes = {
            VideoTaskShape.TEXT2VIDEO,
            VideoTaskShape.IMAGE2VIDEO,
            VideoTaskShape.FIRST_LAST_FRAME,
            VideoTaskShape.MULTIMODAL,
            VideoTaskShape.VIDEO_EDIT,
            VideoTaskShape.VIDEO_EXTEND,
        }
        self.supported_resolutions = ["480p", "720p"]
        self.supports_generate_audio = True
        self.max_reference_total = self.MAX_REFERENCE_TOTAL
        self.card = ModelCard(
            description=node.description
            or "字节跳动 Seedance 2.5 统一视频生成:最长 30 秒、最多 50 多模态参考、支持编辑/延长",
            strengths=[
                "最长 30 秒连贯直出",
                "最多 50 个多模态参考(图 30 + 视频 10 + 音频 10)",
                "视频编辑 / 视频延长",
                "输出 mp4 或 mov",
                "中文语义与动作流畅",
            ],
            categories=["短视频", "长镜头", "多素材合成", "视频编辑", "视频延长"],
            weaknesses=[
                "分辨率仅 480p / 720p(无 1080p/4K)",
                "不支持 camera_fixed(固定镜头;官方仅 Seedance 1.x)",
                "编辑/延长/首尾帧须 ratio=adaptive,自定义比例会异步报错",
            ],
            sample_prompts=[
                "图片1为主角,在草原上奔跑 30 秒长镜头",
                "编辑视频1:把背景换成海边日落",
                "延长视频1,接上视频2 的运镜与动作",
            ],
            languages=["zh", "en"],
            speed_hint="slow",
        )

    def validate(self, request: GenerationRequest) -> None:
        # 先做 2.5 专属数量上限(父类 SeedanceVideoModel 只限制视频/音频 ≤3)
        if len(request.images) > self.MAX_IMAGES:
            raise ValidationError(f"{self.display_name} 最多 {self.MAX_IMAGES} 张参考图,当前 {len(request.images)} 张")
        if len(request.video_refs) > self.MAX_VIDEOS:
            raise ValidationError(
                f"{self.display_name} 最多 {self.MAX_VIDEOS} 段参考视频,当前 {len(request.video_refs)} 段"
            )
        if len(request.audio_refs) > self.MAX_AUDIOS:
            raise ValidationError(
                f"{self.display_name} 最多 {self.MAX_AUDIOS} 段参考音频,当前 {len(request.audio_refs)} 段"
            )

        # 跳过 SeedanceVideoModel.validate 的 3 段音视频上限,直接走通用基类 + 本类约束
        from ...core.base.video import VideoGenerationBase

        VideoGenerationBase.validate(self, request)

        # 官方仅 1.x 支持 camera_fixed;2.5 强校验会 400 InvalidParameter
        if request.camera_fixed:
            raise ValidationError(
                f"{self.display_name} 不支持 camera_fixed(固定镜头);该参数仅 Seedance 1.0 / 1.5 可用,请关闭后重试"
            )

        task_mode = str((request.params or {}).get("task_mode") or "auto").strip().lower()
        frame_mode = str((request.params or {}).get("frame_mode") or "auto").strip().lower()
        duration = request.duration
        ratio = (request.ratio or "").strip().lower()
        n_img = len(request.images)
        has_av = bool(request.video_refs or request.audio_refs)

        # duration=-1 = 自动时长,文生 / 多参考 / 首尾帧 / 编辑 / 延长均合法
        if duration is not None and duration != 0 and duration != -1 and not (4 <= int(duration) <= 30):
            raise ValidationError(f"{self.display_name} 时长须为 4~30 秒或 -1(自动),当前 {duration}")

        # 官方约束:视频编辑 / 延长 / 首帧·首尾帧 必须 ratio=adaptive,自定义比例会异步报错
        # 多模态参考(带视频/音频,或 frame_mode=reference)与纯文生可用自定义比例
        is_edit_or_extend = task_mode in ("edit", "extend")
        is_frame_driven = (
            not has_av and frame_mode != "reference" and n_img >= 1 and task_mode == "auto"
        ) or frame_mode == "first_last"
        if (is_edit_or_extend or is_frame_driven) and ratio and ratio != "adaptive":
            raise ValidationError(
                f"{self.display_name} 在视频编辑/延长/首帧·首尾帧任务下须使用 ratio=adaptive,"
                f"当前 {request.ratio}(自定义比例会触发上游异步报错)"
            )

        if is_edit_or_extend and not request.video_refs:
            # ordered_content 里也可能只挂了视频
            from ...core.schema.types import MediaKind, ContentItemType

            oc_has_video = any(
                item.type == ContentItemType.VIDEO or (item.media is not None and item.media.kind == MediaKind.VIDEO)
                for item in (request.ordered_content or [])
            )
            if not oc_has_video:
                raise ValidationError(f"{self.display_name} 的 {task_mode} 任务至少需要 1 段参考视频")

    async def unavailable_reason(self) -> str:
        return (
            f"{self.display_name} 无可用供应商:请在 Web 控制台配置 "
            "Seedance_Enable_ark + Seedance_apikey_ark(复用火山方舟 Key)"
        )


class Wan22VideoModel(VideoPipelineModel):
    """Wan 2.2:仅支持 首帧 + 尾帧 两类图,无音视频参考,无有声,≤720p(像素积)

    2026-07 收紧:Wan 2.2 仅消费首/尾帧,不再假装支持 0~9 张参考图。
    输入图片上限 = 2(首帧 + 尾帧);超过 2 张请改走 Seedance 2.0 系列。
    """

    MAX_PIXELS = 1280 * 720
    MAX_IMAGES = 2  # 首帧 + 尾帧(尾帧可缺省)

    def __init__(self, node: NodeDef) -> None:
        super().__init__(node)
        self.supported_shapes = {
            VideoTaskShape.TEXT2VIDEO,
            VideoTaskShape.IMAGE2VIDEO,
            VideoTaskShape.FIRST_LAST_FRAME,
        }
        self.supported_ratios = []  # 空 = 任意(靠 width/height 自由指定)
        self.supports_generate_audio = False
        # Wan 2.2 至多消费首帧+尾帧两张图;多参考留给 Seedance 2.0 系列
        self.max_reference_total = self.MAX_IMAGES
        self.card = ModelCard(
            description=node.description or "Wan 2.2 本地 ComfyUI 视频生成,支持文生/图生/首尾帧",
            strengths=["本地部署零 API 费用", "中文支持良好", "宽高比可任意指定"],
            categories=["短视频", "动画", "创意视频"],
            weaknesses=[
                "仅支持首尾帧(≤2 张图),不支持多素材参考",
                "最高 720p",
                "无有声视频",
            ],
            speed_hint="normal",
        )

    def validate(self, request: GenerationRequest) -> None:
        # 音视频参考直接拒绝并给出替代建议(而非静默忽略)
        if request.video_refs or request.audio_refs:
            raise ValidationError("Wan 2.2 不支持视频/音频参考,请改用 Seedance 2.0(模型名 seedance2)")
        # 图片数量硬上限:超过 2 张明确拒绝并指向 Seedance(避免 schema 静默丢弃多余图)
        n_imgs = len(request.images)
        if n_imgs > self.MAX_IMAGES:
            raise ValidationError(
                f"Wan 2.2 仅支持首帧 + 尾帧(最多 {self.MAX_IMAGES} 张),"
                f"当前传入 {n_imgs} 张;≥3 张请改用 Seedance 2.0 / Seedance 2.0 Fast"
            )
        super().validate(request)
        if (request.resolution or "").lower() in ("1080p", "4k"):
            raise ValidationError(f"Wan 2.2 最高支持 720p,不支持 {request.resolution}")
        if request.width * request.height > self.MAX_PIXELS:
            raise ValidationError(f"Wan 2.2 最高支持 720p(约 92 万像素),当前 {request.width}x{request.height} 超限")


class HappyHorseVideoModel(VideoPipelineModel):
    """HappyHorse 1.1:文生 / 图生(首帧) / 多图参考 / 视频编辑 统一入口

    对外模型名固定 ``happyhorse1.1``;内部按输入自动映射:
    - 0 图 → happyhorse-1.1-t2v
    - 1 图 → happyhorse-1.1-i2v(首帧)
    - 2~9 图 / frame_mode=reference / 视频作参考 → happyhorse-1.1-r2v
    - 显式 task_mode=edit → happyhorse-1.1-video-edit
    """

    supports_remote_cancel = True  # DashScope POST /tasks/{id}/cancel(仅 PENDING)

    MAX_IMAGES = 9
    MAX_VIDEOS = 1

    def __init__(self, node: NodeDef) -> None:
        super().__init__(node)
        self.supported_shapes = {
            VideoTaskShape.TEXT2VIDEO,
            VideoTaskShape.IMAGE2VIDEO,
            VideoTaskShape.MULTIMODAL,
            VideoTaskShape.FIRST_LAST_FRAME,  # 降级为 r2v
            VideoTaskShape.VIDEO_EDIT,
        }
        self.supported_resolutions = ["480p", "720p", "1080p"]
        self.supported_ratios = [
            "16:9",
            "9:16",
            "1:1",
            "4:3",
            "3:4",
            "4:5",
            "5:4",
            "9:21",
            "21:9",
        ]
        # 原生输出有声视频且无 generate_audio 开关;标 True 避免
        # GenerationRequest.generate_audio 默认 True 触发「不支持有声」误拒。
        self.supports_generate_audio = True
        self.max_reference_total = self.MAX_IMAGES + self.MAX_VIDEOS
        self.card = ModelCard(
            description=node.description or "阿里云 HappyHorse 1.1 统一视频生成,按输入自动切换文生/图生/参考/编辑",
            strengths=[
                "文生/图生/多参考一体",
                "物理真实运动流畅",
                "最高 1080P、最长 15 秒",
                "参考图可用 [Image N] 在 prompt 中引用",
            ],
            categories=["短视频", "电商广告", "角色一致性", "视频编辑"],
            weaknesses=["无首尾帧专用端点(多图走参考生)", "不支持参考音频", "视频编辑须显式选择编辑模式"],
            sample_prompts=[
                "一只猫在草地上奔跑",
                "[Image 1]中的女性优雅转身,展开[Image 2]中的折扇",
            ],
            languages=["zh", "en"],
            speed_hint="slow",
        )

    def validate(self, request: GenerationRequest) -> None:
        if request.audio_refs:
            raise ValidationError(f"{self.display_name} 不支持参考音频,请移除音频素材")
        if len(request.video_refs) > self.MAX_VIDEOS:
            raise ValidationError(f"{self.display_name} 最多 1 段输入视频,当前 {len(request.video_refs)} 段")
        if len(request.images) > self.MAX_IMAGES:
            raise ValidationError(f"{self.display_name} 最多 {self.MAX_IMAGES} 张参考图,当前 {len(request.images)} 张")
        task_mode = str((request.params or {}).get("task_mode") or "auto").strip().lower()
        frame_mode = str((request.params or {}).get("frame_mode") or "auto").strip().lower()
        is_edit = task_mode == "edit" or frame_mode == "edit"
        from ...core.schema.types import MediaKind, ContentItemType

        oc_has_video = any(
            item.type == ContentItemType.VIDEO or (item.media is not None and item.media.kind == MediaKind.VIDEO)
            for item in (request.ordered_content or [])
        )
        if not is_edit and (request.video_refs or oc_has_video):
            raise ValidationError(
                f"{self.display_name} 仅在「视频编辑」模式下接受输入视频;"
                "多参考 / 首尾帧请移除视频,或切换到视频编辑"
            )
        if is_edit:
            if not request.video_refs and not oc_has_video:
                raise ValidationError(f"{self.display_name} 的视频编辑模式需要 1 段输入视频")
            res = request.resolution or request.params.get("resolution") or "1080p"
            if str(res).lower() in ("480p", "4k"):
                raise ValidationError(f"{self.display_name} 视频编辑仅支持 720p / 1080p,当前 {res}")
        super().validate(request)

    def channel_bindings(self) -> list[ChannelBinding]:
        node = self.node
        builtins = builtin_happyhorse_channels()
        external = channel_registry.bindings_for(node.name)

        if node.provider:
            ch = builtins.get(node.provider)
            if ch is not None:
                return [ChannelBinding(ch, vendor_model=None)]
            return [b for b in external if b.channel.name == node.provider]

        bindings: list[ChannelBinding] = []
        for _name, ch in builtins.items():
            # vendor_model 留空:通道内按形态自动解析 t2v/i2v/r2v/edit
            bindings.append(ChannelBinding(ch, vendor_model=None))
        bindings.extend(external)
        return bindings

    async def execute_on_channel(
        self,
        request: GenerationRequest,
        binding: ChannelBinding,
        *,
        on_progress: Optional[ProgressCallback] = None,
    ) -> NodeOutput:
        return await binding.channel.invoke(
            request=request,
            node=self.node,
            on_progress=on_progress,
            vendor_model=binding.vendor_model,
        )

    async def unavailable_reason(self) -> str:
        return (
            f"{self.display_name} 无可用供应商:请在 Web 控制台配置 "
            "HappyHorse_apikey_dashscope 并启用 HappyHorse_Enable_dashscope"
        )


__all__ = [
    "SeedanceVideoModel",
    "Seedance25VideoModel",
    "Wan22VideoModel",
    "HappyHorseVideoModel",
]
