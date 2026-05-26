"""资源路径常量"""

import sys
import json
import random
import shutil
from pathlib import Path

from gsuid_core.data_store import get_res_path

MAIN_PATH = get_res_path() / "RHComfyUI"
sys.path.append(str(MAIN_PATH))

# 配置文件
CONFIG_PATH = MAIN_PATH / "config.json"

_CP_WORKFLOW_PATH = Path(__file__).parent / "workflow"
WORKFLOW_PATH = MAIN_PATH / "workflow"
OUTPUT_PATH = MAIN_PATH / "output"

# Pipeline 定义文件（内置，随插件发布）
_CP_PIPELINES_PATH = Path(__file__).parent / "pipelines"
# 运行时 Pipeline 路径（用户可自定义扩展）
PIPELINES_PATH = MAIN_PATH / "pipelines"

DRAW_TEXT_WORKFLOW_PATH = WORKFLOW_PATH / "文生图"
DRAW_IMAGE_WORKFLOW_PATH = WORKFLOW_PATH / "图生图"
EDIT_WORKFLOW_PATH = WORKFLOW_PATH / "图片编辑"
MUSIC_WORKFLOW_PATH = WORKFLOW_PATH / "音乐生成"
SPEECH_WORKFLOW_PATH = WORKFLOW_PATH / "语音生成"

VIDEO_BY_TEXT_WORKFLOW_PATH = WORKFLOW_PATH / "文生视频"
VIDEO_BY_IMAGE_WORKFLOW_PATH = WORKFLOW_PATH / "图生视频"


def load_workflow(path: Path) -> dict:
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
        EDIT_WORKFLOW_PATH,
        DRAW_TEXT_WORKFLOW_PATH,
        DRAW_IMAGE_WORKFLOW_PATH,
        VIDEO_BY_TEXT_WORKFLOW_PATH,
        VIDEO_BY_IMAGE_WORKFLOW_PATH,
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

    # 将内置 pipelines 复制到运行时路径
    if _CP_PIPELINES_PATH.exists():
        for _dir in _CP_PIPELINES_PATH.iterdir():
            if _dir.is_dir():
                dest_dir = PIPELINES_PATH / _dir.name
                dest_dir.mkdir(parents=True, exist_ok=True)
                for _file in _dir.iterdir():
                    dest_file = dest_dir / _file.name
                    shutil.copy2(_file, dest_file)


init_dir()
