"""资源路径常量"""

import sys
import json
import random
import shutil
from typing import Any
from pathlib import Path

from gsuid_core.data_store import get_res_path

MAIN_PATH = get_res_path() / "RHComfyUI"
sys.path.append(str(MAIN_PATH))

# 配置文件
SERVICE_CONFIG_PATH = MAIN_PATH / "service_config.json"
PLUGIN_CONFIG_PATH = MAIN_PATH / "plugin_config.json"

_CP_WORKFLOW_PATH = Path(__file__).parent / "workflow"
WORKFLOW_PATH = MAIN_PATH / "workflow"
OUTPUT_PATH = MAIN_PATH / "output"

# 2026-07 起模型定义全编程式(models/*/defs.py),不再有内置 pipelines YAML;
# 运行时路径仅保留目录常量供旧数据排查,启动时不再加载
PIPELINES_PATH = MAIN_PATH / "pipelines"

# 图片生成工作流(文生图/图生图/图片编辑)统一放在一个目录下,
# YAML 节点按 inputs 端口自动决定是否接受参考图。
IMAGEGEN_WORKFLOW_PATH = WORKFLOW_PATH / "图片生成"
# 向后兼容别名(供老代码或自定义 mapper 仍可使用)
DRAW_TEXT_WORKFLOW_PATH = IMAGEGEN_WORKFLOW_PATH
DRAW_IMAGE_WORKFLOW_PATH = IMAGEGEN_WORKFLOW_PATH
EDIT_WORKFLOW_PATH = IMAGEGEN_WORKFLOW_PATH
MUSIC_WORKFLOW_PATH = WORKFLOW_PATH / "音乐生成"
SPEECH_WORKFLOW_PATH = WORKFLOW_PATH / "语音生成"

# 视频生成工作流:文生 / 图生 共用一个目录(逻辑上属于同一类任务)
# - wan2.2_t2v.json   → 0 张图(纯文生视频)
# - wan2.2_i2v.json   → 1+ 张图(图生视频,首帧)
VIDEO_WORKFLOW_PATH = WORKFLOW_PATH / "视频生成"
# 向后兼容别名(供老代码或自定义 mapper 仍可使用)
VIDEO_BY_TEXT_WORKFLOW_PATH = VIDEO_WORKFLOW_PATH
VIDEO_BY_IMAGE_WORKFLOW_PATH = VIDEO_WORKFLOW_PATH


def load_workflow(path: Path) -> dict[str, Any]:
    """加载工作流 JSON 并随机化 seed"""
    with open(path, "r", encoding="utf-8") as f:
        workflow = json.load(f)
    for i in workflow:
        if workflow[i]["class_type"] == "RandomNoise":
            workflow[i]["inputs"]["noise_seed"] = random.randint(0, 1000000000)
        if "seed" in list(workflow[i]["inputs"].keys()):
            workflow[i]["inputs"]["seed"] = random.randint(0, 1000000000)
    return workflow


def init_dir() -> None:
    """初始化目录结构"""
    for i in [
        MAIN_PATH,
        WORKFLOW_PATH,
        OUTPUT_PATH,
        PIPELINES_PATH,
        IMAGEGEN_WORKFLOW_PATH,
        VIDEO_WORKFLOW_PATH,
        MUSIC_WORKFLOW_PATH,
        SPEECH_WORKFLOW_PATH,
    ]:
        i.mkdir(parents=True, exist_ok=True)

    # 将 workflow 中的文件复制到 MAIN_PATH/workflow 中
    for _dir in _CP_WORKFLOW_PATH.iterdir():
        for _file in _dir.iterdir():
            pa = WORKFLOW_PATH / _dir.name / _file.name
            pa.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_file, pa)


init_dir()
