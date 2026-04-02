"""工作流字典"""

from .models import MODEL_REGISTRY

# 工作流字典
text2image_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.task_type == "text2image"}

image2image_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.task_type == "image2image"}

image_edit_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.task_type == "image_edit"}

music_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.task_type == "music"}

speech_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.task_type == "speech"}

text2video_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.task_type == "text2video"}

image2video_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.task_type == "image2video"}
