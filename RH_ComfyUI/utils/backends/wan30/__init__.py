"""阿里云 DashScope 万相 3.0 视频生成后端

对外模型名 ``wan3.0``,供应商 model 固定 ``wan3.0-video``。
凭证复用 HappyHorse_*_dashscope;须在 DashScope_Enabled_Models 勾选 wan3.0。

形态对齐 Seedance 2.0:
- 0 图 → 文生
- 1 张图 → 图生(首帧)
- 2 张图 → 首尾帧
- 图+音/视频,或 frame_mode=reference → 全能参考
另多:file_url / link_url 参考文件或网页生视频(与首尾帧互斥)。
"""

from .channel import Wan30Channel, builtin_wan30_channels
from .classify import classify_wan30

__all__ = [
    "Wan30Channel",
    "builtin_wan30_channels",
    "classify_wan30",
]
