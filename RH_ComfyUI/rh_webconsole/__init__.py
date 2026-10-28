"""RH_ComfyUI Web 控制台页 — 模型 / 供应商全景看板。

在 import 时登记页面:Hub 的 `/plugins`(插件配置界面)在插件标题右侧显示按钮,
点开后以 iframe 嵌入 `/plugin-pages/rh_comfyui/dashboard/`。
"""

from pathlib import Path

from gsuid_core.webconsole.plugin_page import register_plugin_page

WEB_DIR = Path(__file__).parent.parent / "web"

register_plugin_page(
    title="模型与供应商看板",
    static_dir=WEB_DIR,
    page_id="dashboard",
    plugin="RH_ComfyUI",
    description="用图表查看全部模型的可用性、供应商状态、供应商顺序与继承的模型。",
    confirm_message="即将打开 RH_ComfyUI 的模型与供应商看板。确认后侧边栏会收起，页面以嵌入方式打开。",
    title_i18n={
        "zh-CN": "模型与供应商看板",
        "en-US": "Model & Provider Board",
        "ja-JP": "モデル・プロバイダボード",
    },
    description_i18n={
        "zh-CN": "用图表查看全部模型的可用性、供应商状态、供应商顺序与继承的模型。",
        "en-US": "Charts for every model's availability, provider status, provider order and inherited models.",
        "ja-JP": "全モデルの可用性、プロバイダ状態、優先順位、継承モデルをグラフで確認できます。",
    },
    confirm_message_i18n={
        "zh-CN": "即将打开 RH_ComfyUI 的模型与供应商看板。确认后侧边栏会收起，页面以嵌入方式打开。",
        "en-US": "Open the RH_ComfyUI model & provider board. The sidebar will collapse and the page will be embedded.",
        "ja-JP": "RH_ComfyUI のモデル・プロバイダボードを開きます。サイドバーは折りたたまれ埋め込み表示されます。",
    },
    icon="activity",
)

from . import api as _api  # noqa: E402, F401

__all__ = ["WEB_DIR"]
