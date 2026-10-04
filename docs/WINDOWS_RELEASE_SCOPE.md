# Windows 发布范围

> 重建说明：本文件原文因编码问题损坏，无法从历史恢复（自初始提交起即已损坏）。
> 本稿依据仓库中其他文档（README、分发规划、Windows 单文件分发说明、发布检查清单）已有的事实重建，
> 只陈述这些文档已经确认的内容。**需项目负责人复核。**

## 本阶段发布范围

- Edge / Chrome 浏览器扩展（Manifest V3，仅 `storage` 与 `http://127.0.0.1:8765/*` 权限）；
- Windows 本地语音服务：Python runtime、FastAPI 服务、FunASR、中文批量与流式识别模型、VAD；
- 医学热词包、模板、提交前核对、本地反馈；
- Windows 离线内测包与自解压安装器（见 `WINDOWS_EXECUTABLE_DISTRIBUTION.md`）；
- Edge Add-ons 上传用的扩展 ZIP（根目录直接包含 `manifest.json`）。

## 本阶段不在范围内

- Linux；
- 方言识别模型（见 `ASR_IMPROVEMENT_NOTES.md`：暂不引入）；
- 正式商业版授权与机构管理（`/license/status` 目前为占位）；
- 代码签名、自动更新、完整托盘服务（见 `WINDOWS_EXECUTABLE_DISTRIBUTION.md` 的“当前限制”）。

macOS Apple Silicon 的构建脚本在 `packaging/macos/`，需在 Apple Silicon Mac 上构建，不属于 Windows 发布流程。

## 发布前检查

1. 运行 `scripts/check_release.ps1`；
2. 运行 `python -m unittest discover -s tests -t .`（回归测试）；
3. 确认扩展 ZIP 根目录包含 `manifest.json`；
4. 在干净的 Windows 环境安装离线包，确认 `http://127.0.0.1:8765/health` 正常返回；
5. 确认隐私政策、商店文案与实际行为一致（见 `RELEASE_CHECKLIST.md`）。
