"""Vertex 服务账号 JSON 使用控制台文件上传。"""

from __future__ import annotations

from RH_ComfyUI.rh_config.service_config import SERVICE_CONFIG_DEFAULT
from gsuid_core.utils.plugins_config.models import GsFileUploadConfig


def test_gemini_sa_file_is_upload() -> None:
    item = SERVICE_CONFIG_DEFAULT["Gemini_Image_SA_File"]
    assert isinstance(item, GsFileUploadConfig)
    assert item.data == ""
    assert item.filename == "vertex_sa"
    assert item.suffix == "json"
    assert item.upload_to.replace("\\", "/").endswith("RHComfyUI/gemini")
