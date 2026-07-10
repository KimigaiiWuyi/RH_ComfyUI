"""供应商对账命令 — 聚合行 + 熔断快照的渲染(DB 聚合由 SQL 层保证,单测渲染纯函数)"""

from RH_ComfyUI.rh_admin.commands import _render_provider_stats


def _rows():
    return [
        {
            "provider": "ark",
            "total": 40,
            "success": 38,
            "failed": 2,
            "success_rate": 0.95,
            "avg_elapsed_ms": 52000,
            "total_points": 600,
        },
        {
            "provider": "baidu",
            "total": 10,
            "success": 6,
            "failed": 4,
            "success_rate": 0.6,
            "avg_elapsed_ms": 8000,
            "total_points": 20,
        },
    ]


def test_render_provider_stats_basic():
    text = _render_provider_stats(_rows(), {}, "最近 7 天")
    assert "最近 7 天" in text
    assert "ark: 40单 成功率95.0%" in text
    assert "baidu: 10单 成功率60.0%" in text
    assert "均耗时52000ms" in text and "消耗600积分" in text
    assert "🟢" in text  # 无熔断快照 → 全通道健康


def test_render_provider_stats_with_circuit_snapshot():
    snapshot = {
        "seedance2/baidu": {"failure_count": 3, "circuit_open": True},
        "banana2/gemini": {"failure_count": 1, "circuit_open": False},
    }
    text = _render_provider_stats(_rows(), snapshot, "全部时间")
    assert "🔴" in text and "seedance2/baidu" in text
    assert "🟡" in text and "banana2/gemini×1" in text


def test_render_provider_stats_empty():
    text = _render_provider_stats([], {}, "全部时间")
    assert "暂无" in text
