"""RH_ComfyUI Web 控制台 — HTTP 接口(PluginAPI 收口)。

只读接口,不改动任何模型/通道状态。响应统一 ``{status, msg, data}`` 信封。
"""

from __future__ import annotations

from gsuid_core.webconsole.plugin_page import ApiOk, PluginAPI, api_ok

from .dashboard import DashboardSnapshot, build_dashboard_snapshot

api = PluginAPI(plugin="RH_ComfyUI", prefix="/api/RH_ComfyUI/dashboard")


@api.get("/overview")
async def overview() -> ApiOk:
    """一次取回看板所需的全部数据。

    ``data`` 结构::

        {
            "generated_at": "2026-07-21 10:00:00",
            "balancer": {"mode": "round_robin", "failure_threshold": 3, "circuit_breaker_seconds": 120.0},
            "totals": {
                "models": 32,
                "models_available": 27,
                "providers": 11,
                "providers_healthy": 9,
                "links": 45,
                "links_available": 39,
                "circuit_open": 1,
            },
            "modalities": [{"key": "image", "label": "图片生成", "total": 14, "available": 12}],
            "providers": [
                {
                    "name": "ark",
                    "label": "火山方舟 ARK",
                    "weight": 3,
                    "model_count": 5,
                    "available_model_count": 4,
                    "unavailable_model_count": 1,
                    "fully_available": false,
                    "supports_remote_cancel": true,
                    "inflight": 0,
                    "concurrency_limit": 10,
                    "failure_total": 0,
                    "circuit_open_models": [],
                    "models": ["seedance2"],
                }
            ],
            "models": [
                {
                    "name": "seedance2",
                    "display_name": "Seedance 2.0",
                    "catalog_group": "video",
                    "group_label": "视频生成",
                    "backend": "seedance",
                    "icon": "doubao",
                    "available": true,
                    "unavailable_reason": "",
                    "priority": 50,
                    "point_cost": 15,
                    "point_min": 15,
                    "point_max": 30,
                    "execution_mode": "async_poll",
                    "supports_cancel": true,
                    "supports_remote_cancel": true,
                    "channels": [
                        {
                            "name": "ark",
                            "position": 0,
                            "vendor_model": "...",
                            "weight": 3,
                            "available": true,
                            "supports_cancel": true,
                            "supports_remote_cancel": true,
                            "failure_count": 0,
                            "circuit_open": false,
                            "inflight": 0,
                            "concurrency_limit": 10,
                        }
                    ],
                }
            ],
        }

    ``channels[].position`` 是该模型的供应商**声明顺序**,即负载均衡排序的输入顺序;
    ``providers[]`` 是这些通道按供应商维度的反向聚合(某家继承了哪些模型)。
    """
    snapshot: DashboardSnapshot = await build_dashboard_snapshot()
    return api_ok(snapshot)


__all__ = ["api", "overview"]
