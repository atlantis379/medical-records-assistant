# 第三方软件与模型许可说明

更新日期：2026-06-24

> 本文件用于发布前许可审计，不构成法律意见。正式发布前应核对实际打包内容、模型缓存路径和依赖版本。

## 主要组件

| 组件 | 当前用途 | 已知许可证 | 备注 |
|---|---|---|---|
| FunASR | 本地语音识别框架 | MIT | 需保留版权和许可证文本 |
| Paraformer 中文批量 ASR 模型 | 中文语音转写 | Apache License 2.0（以模型 README/ModelScope 页面为准） | 当前项目默认模型 |
| Paraformer 中文流式 ASR 模型 | 流式语音转写 | Apache License 2.0（以模型 README/ModelScope 页面为准） | 当前项目流式模型 |
| FSMN-VAD | 语音活动检测 | Apache License 2.0（以模型 README/ModelScope 页面为准） | 批量识别 VAD |
| FastAPI / Uvicorn | 本地 HTTP/WebSocket 服务 | 以依赖包许可证为准 | 发布安装包前自动生成依赖清单 |
| NumPy | 音频数据处理 | BSD 系列许可证 | 发布安装包前自动生成依赖清单 |
| RapidOCR（rapidocr-onnxruntime 1.4.4）及其中的 PP-OCRv4 中文轻量识别模型 | 外院报告图片的文字识别（本机 CPU） | Apache License 2.0（以各自发布页面为准） | 需保留版权和许可证文本；模型随 Python 包分发，约 16 MB |
| OpenCV（opencv-python-headless 4.10）、onnxruntime、Pillow、Shapely、pyclipper | 图片解码和识别的运行环境 | Apache 2.0 / MIT / HPND / BSD（以各包为准） | 发布安装包前自动生成依赖清单；OpenCV 的视频组件（ffmpeg 库）在打包时删除，不使用 |
| Microsoft.Web.WebView2（SDK 的两个 .NET 库和加载器） | 听写窗口（packaging/windows/vendor/webview2，随启动器分发） | Microsoft 的 BSD 式许可证，文本见该目录 LICENSE.txt / NOTICE.txt | 只含 SDK，不含 WebView2 运行时；运行时是 Windows 自带组件，缺失时启动器改用浏览器 |

## Apache License 2.0 发布注意事项

- 保留许可证文本；
- 保留版权声明；
- 如组件包含 NOTICE 文件，应随发布包保留；
- 不暗示原作者或模型发布方为本产品背书；
- 如修改模型或源码，应说明修改。

## MIT 发布注意事项

- 保留版权声明；
- 保留许可证文本。

## 发布前待办

- [ ] 生成 Python 依赖许可证清单；
- [ ] 将依赖许可证文本纳入安装包或下载页；
- [ ] 固定模型名称、版本/commit/hash；
- [ ] 记录模型下载源和大小；
- [ ] 确认商业使用条款无新增限制。