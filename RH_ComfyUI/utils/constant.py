# ===== 模型优先级定义 =====
# 优先使用 qwen，banana 模型只在明确要求高质量时才使用
MODEL_PRIORITY = {
    "text2image": ["qwen_2512", "banana2", "banana_pro"],
    "image2image": ["qwen_2512_img2img"],
    "image_edit": ["qwen_2511", "banana2", "banana_pro"],
    "text2video": ["wan2.2_text2video"],
    "image2video": ["wan2.2_img2video"],
    "music": ["ace_step1.5"],
    "speech": ["IndexTTS2"],
}
