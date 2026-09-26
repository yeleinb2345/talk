# LuminaGrade 本地调色

一个面向剪辑画质风格的 Windows 本地视频调色工具。它不是 AI 修复/超分软件，而是使用 FFmpeg 的传统图像处理：曝光、对比度、饱和度、Unsharp Mask 锐化、色温，以及基于模糊 + Screen 混合的 Glow/Bloom。

## 主要效果

- **剪辑圈亮透**：通透、发亮、略锐，适合作为默认起点
- **蓝调清透**：轻微偏冷，强化边缘清晰感
- **暖白发光**：暖色高光 + 更明显 Bloom
- **柔和高光**：较柔的锐化和更大的发光半径
- 原片/处理后双栏预览
- 完整视频导出 MP4（H.264 + AAC）
- 正式 Windows EXE 内置 `ffmpeg.exe` 与 `ffprobe.exe`，处理时无需联网

## Windows EXE

GitHub Actions 会在 Windows Runner 上通过 PyInstaller 生成单文件 `LuminaGrade.exe`，构建产物上传为 Actions Artifact `LuminaGrade-Windows`。

## 说明

Glow 是传统后期思路的近似实现，不包含或复制任何第三方付费插件代码。具体观感会受素材曝光、码率、肤色与光源影响；建议用预设起步，再微调 Glow 强度、半径和锐化。
