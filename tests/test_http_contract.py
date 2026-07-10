"""HTTP 契约快照(golden test)— 把"契约只增不改"从人肉约定变成 CI 强制

/RH_ComfyUI/models 系列接口的既有字段是对外契约:字段只能新增,不能改名、
删除或变类型。本文件冻结 ModelEntry 与目录顶层结构的字段快照;若某次改动
让这里失败,说明它破坏了下游(前端/画布/Agent)的兼容性 —— 正确做法是
新增字段,而不是修改本快照迁就实现。
"""

from RH_ComfyUI.rh_models.api import ModelEntry

# ModelEntry.to_dict() 的冻结快照:字段名 → 类型。只增不改。
_MODEL_ENTRY_GOLDEN: dict[str, type] = {
    "name": str,
    "display_name": str,
    "task_type": str,
    "backend": str,
    "backend_model": type(None),  # Optional[str], 默认 None
    "description": str,
    "point_cost": int,
    "priority": int,
    "available": bool,
    "unavailable_reason": type(None),  # Optional[str], 默认 None
    "supported_tasks": list,
    "input_schema": dict,
    "output_schema": dict,
    "requirements": list,
    "card": dict,
    "channels": list,
    "execution_mode": str,
    "accepts_images": bool,
    "max_input_images": int,
}


def test_model_entry_contract_fields_frozen():
    entry = ModelEntry(name="m", display_name="M", task_type="image", backend="b").to_dict()
    for field_name, expected_type in _MODEL_ENTRY_GOLDEN.items():
        assert field_name in entry, f"契约字段被删除/改名: {field_name}"
        assert isinstance(entry[field_name], expected_type), (
            f"契约字段类型改变: {field_name} 期望 {expected_type.__name__}, 实际 {type(entry[field_name]).__name__}"
        )
    # 新增字段合法,但必须有意识地补进快照(保证本测试持续覆盖全量契约)
    extra = set(entry) - set(_MODEL_ENTRY_GOLDEN)
    assert not extra, f"ModelEntry 新增了未冻结进契约快照的字段: {sorted(extra)}"


def test_model_entry_optional_fields_accept_str():
    # backend_model / unavailable_reason 是 Optional[str]:赋值后必须序列化为 str
    entry = ModelEntry(
        name="m",
        display_name="M",
        task_type="image",
        backend="b",
        backend_model="vendor-x",
        unavailable_reason="缺配置",
    ).to_dict()
    assert isinstance(entry["backend_model"], str)
    assert isinstance(entry["unavailable_reason"], str)


def test_channels_item_contract():
    # channels 列表项结构:{"name": str, "vendor_model": Optional[str], "available": bool}
    # (rh_models/api.py 两个 _build_entry* 构造点都按此形状产出)
    entry = ModelEntry(
        name="m",
        display_name="M",
        task_type="image",
        backend="b",
        channels=[{"name": "ark", "vendor_model": "seedance-2", "available": True}],
    ).to_dict()
    item = entry["channels"][0]
    assert set(item) >= {"name", "vendor_model", "available"}
    assert isinstance(item["name"], str) and isinstance(item["available"], bool)


def test_catalog_top_level_contract():
    # build_model_catalog 顶层结构快照(webapi 的 ModelCatalog 响应模型同源)
    import asyncio

    from RH_ComfyUI.rh_models.api import build_model_catalog

    catalog = asyncio.run(build_model_catalog(include_unavailable=True))
    for key, expected_type in {
        "task_types": list,
        "task_display": dict,
        "total": int,
        "available_count": int,
        "models": list,
    }.items():
        assert key in catalog, f"目录契约字段缺失: {key}"
        assert isinstance(catalog[key], expected_type)
    # 目录内每个模型条目也必须满足 ModelEntry 冻结快照的字段集
    for m in catalog["models"]:
        missing = set(_MODEL_ENTRY_GOLDEN) - set(m)
        assert not missing, f"目录条目缺契约字段: {sorted(missing)}"
