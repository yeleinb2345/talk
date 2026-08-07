# 商家素材管家 v1.1

面向电商卖家的 Windows 本地素材整理 / 批处理工具。默认完全离线，OpenAI 仅用于可选命名建议。

## 当前功能

- 递归扫描 JPG/JPEG/PNG/WebP/BMP/TIFF 和常见视频素材
- 图片尺寸、文件大小、SKU 初步识别
- 图片缩略图预览，双击素材可打开所在目录
- SHA-256 精确重复文件检测；先按文件大小分组再计算哈希
- 重复素材“复制到隔离目录”，默认不删除原文件
- 批量改名，执行前可预览
- 批量压缩、改尺寸、转 WebP/JPG/PNG
- 按 SKU 自动复制归档
- 导出 CSV 素材清单
- 任务日志与进度条
- 记住图片输出参数、命名前缀、AI 模型等非敏感设置
- 可选 OpenAI Responses API 商品素材命名建议
- OpenAI API Key 不写入本地配置文件

## Windows 本地构建

双击或在终端执行：

```bat
build_windows.bat
```

输出：`dist/商家素材管家.exe`

## GitHub Actions 构建

工程配套根目录 `.github/workflows/build-windows.yml`。在 `ecommerce-media-tool` 分支推送后，Windows runner 会生成单文件 EXE，并检查文件小于 1GB。

## 安全设计

- 批量转换输出到新目录，不覆盖原素材。
- SKU 归档为复制模式，不移动原素材。
- 重复文件隔离为复制模式，不自动删除。
- 改名是唯一会直接修改原文件名的动作，执行前会二次确认，并提供预览。
- AI 默认关闭，只有用户主动填写 API Key 并点击 AI 功能时才联网。
- AI 命名功能只发送用户当前选中的文件名，不上传素材文件本身。

## 体积

使用 Python + Tkinter + Pillow + PyInstaller，避免 Electron / Chromium / 本地大模型。实际 EXE 一般远低于 1GB。
