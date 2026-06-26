# ????

?????????? Windows ???macOS Apple Silicon ??????????????????????????????????Windows ??????????????????????

---

# macOS Apple Silicon 内测安装包构建说明

此目录用于在 Apple Silicon Mac 上生成可分发的离线内测包。

重要限制：Windows 的 `.venv` 和 Python runtime 不能用于 macOS。必须在一台 Apple Silicon Mac 上运行构建脚本，生成 macOS arm64 版 `.venv-macos` 和 `.pkg`。

## 前置条件

- Apple Silicon Mac（arm64，M1/M2/M3/M4）；
- macOS 12 或更高版本；
- Python 3.12；
- Xcode Command Line Tools，提供 `pkgbuild`；
- 已缓存中文 ASR/VAD 模型，默认读取：

```bash
~/.cache/modelscope/hub/models/iic/
```

需要包含：

```text
speech_fsmn_vad_zh-cn-16k-common-pytorch
speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online
speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch
```

## 构建 pkg

在项目根目录运行：

```bash
bash packaging/macos/build_macos_beta_package.sh
```

输出：

```text
dist/macos/bingli-assistant-macos-arm64-v版本-beta.pkg
```

## 无 pkgbuild 时生成 tar.gz

```bash
CREATE_PKG=0 bash packaging/macos/build_macos_beta_package.sh
```

## 安装后路径

```text
/Library/Application Support/ClinicalDictationAssistant
/Library/LaunchDaemons/com.clinicaldictation.localservice.plist
```

## 卸载

```bash
bash packaging/macos/uninstall_macos.sh
```

## Apple Silicon 限制

构建脚本会检查 `uname -m`，必须是 `arm64`。如果 Terminal 运行在 Rosetta 下，脚本会停止并提示切换到原生 arm64 Terminal。

不再构建 Intel/x86_64 版本。

## 正式外部分发前

当前脚本生成的是未签名、未公证 pkg。正式给院外用户分发前，需要 Apple Developer ID 签名和 notarization；否则 Gatekeeper 可能拦截。
