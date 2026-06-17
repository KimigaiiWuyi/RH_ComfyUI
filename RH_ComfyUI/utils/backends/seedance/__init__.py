"""火山方舟 Seedance 2.0 后端

支持:
- 文生视频(text2video)
- 图生视频 - 首帧(image2video)
- 图生视频 - 首尾帧(first_last_frame2video)
- 多模态参考生视频(multimodal2video):text + 多张图片 + 多段视频 + 多段音频,任意顺序
- 视频编辑(video_edit)
- 样片模式(draft) → 基于 draft 生成正式视频

API 文档:
- https://www.volcengine.com/docs/82379/1520758
- https://www.volcengine.com/docs/82379/2291680

API 端点:
- POST /api/v3/contents/generations/tasks          创建任务
- GET  /api/v3/contents/generations/tasks/{id}     查询任务
- DELETE /api/v3/contents/generations/tasks/{id}   删除/取消任务
- GET  /api/v3/contents/generations/tasks          任务列表
"""
