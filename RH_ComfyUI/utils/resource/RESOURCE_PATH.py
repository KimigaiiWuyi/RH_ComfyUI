import sys
import json
import random
from pathlib import Path

from gsuid_core.data_store import get_res_path

MAIN_PATH = get_res_path() / "RHComfyUI"
sys.path.append(str(MAIN_PATH))

# 配置文件
CONFIG_PATH = MAIN_PATH / "config.json"

_CP_WORKFLOW_PATH = Path(__file__).parent / "workflow"
WORKFLOW_PATH = MAIN_PATH / "workflow"
OUTPUT_PATH = MAIN_PATH / "output"

DRAW_TEXT_WORKFLOW_PATH = WORKFLOW_PATH / "文生图"
DRAW_IMAGE_WORKFLOW_PATH = WORKFLOW_PATH / "图生图"
EDIT_WORKFLOW_PATH = WORKFLOW_PATH / "图片编辑"
VIDEO_WORKFLOW_PATH = WORKFLOW_PATH / "视频生成"
MUSIC_WORKFLOW_PATH = WORKFLOW_PATH / "音乐生成"
SPEECH_WORKFLOW_PATH = WORKFLOW_PATH / "语音生成"


def load_workflow(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        workflow = json.load(f)
    for i in workflow:
        if workflow[i]["class_type"] == "RandomNoise":
            workflow[i]["inputs"]["noise_seed"] = random.randint(0, 1000000000)
        if "seed" in list(workflow[i]["inputs"].keys()):
            workflow[i]["inputs"]["seed"] = random.randint(0, 1000000000)
    return workflow


def init_dir():
    for i in [
        MAIN_PATH,
        WORKFLOW_PATH,
        OUTPUT_PATH,
        EDIT_WORKFLOW_PATH,
        DRAW_TEXT_WORKFLOW_PATH,
        DRAW_IMAGE_WORKFLOW_PATH,
        VIDEO_WORKFLOW_PATH,
        MUSIC_WORKFLOW_PATH,
        SPEECH_WORKFLOW_PATH,
    ]:
        i.mkdir(parents=True, exist_ok=True)

    # 将workflow中的文件复制到MAIN_PATH/workflow中
    for _dir in _CP_WORKFLOW_PATH.iterdir():
        for _file in _dir.iterdir():
            pa = WORKFLOW_PATH / _dir.name / _file.name
            if not pa.exists():
                pa.parent.mkdir(parents=True, exist_ok=True)
                if not pa.exists():
                    with _file.open("rb") as src, pa.open("wb") as dst:
                        dst.write(src.read())


init_dir()
