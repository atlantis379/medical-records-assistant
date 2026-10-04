# Windows 单文件 EXE 分发说明

病历助手当前采用“自解压安装器 EXE”的方式做 Windows 内测分发：

```text
bingli-assistant-setup-v0.10.0-beta.exe
```

这个 EXE 内部包含已经验证过的 Windows 离线包：

- 浏览器扩展目录 `extension/`；
- 本地 FastAPI 语音服务 `server/`；
- Python runtime；
- Python 依赖环境 `.venv/`（只含服务需要的 CPU 版依赖）；
- 中文批量 ASR、中文流式 ASR、VAD 模型；
- 医学热词包、模板、提交前核对和本地反馈模块。

## 启动与停止（给非专业人员）

安装后，医生只需要认识两件事：**双击桌面“病历助手”图标**，和**用完点“停止服务”**。

| 动作 | 怎么做 |
|---|---|
| 启动 | 双击桌面“病历助手”图标。没有黑色窗口；屏幕右下角出现托盘小圆点（黄=启动中，绿=就绪，灰=已停止，红=出错），就绪后自动打开状态页 |
| 开始听写 | 点浏览器右上角的“病历助手”扩展图标 |
| 停止 | 听写页面右上角“停止服务”；或托盘图标右键“停止服务并退出”；或状态页 `http://127.0.0.1:8765/` 的“停止服务”；或开始菜单“停止病历助手服务” |
| 再次启动 | 再双击桌面图标（如果托盘还在，会唤起它并重新启动服务） |

行为约定：

- 停止后草稿仍保留在听写页面；听写进行中点“停止服务”会先提示会中断听写；
- **不会误杀其他程序**：端口 8765 被别的程序占用时，启动器只提示（托盘变红），不会去结束它；停止时只会停止自己确认过身份的服务（`/service/status` 返回 `service: bingli-assistant`）；
- 关闭浏览器页面不会停止服务；托盘“退出托盘（保持服务运行）”只关闭图标；托盘进程被结束时服务继续运行（服务输出写入日志文件，不依赖托盘进程）；
- 只允许一个托盘实例；
- 日志在 `%LOCALAPPDATA%\BingliAssistant\logs\`（`service.log`、`launcher.log`，超过 5 MB 自动轮转一次）；
- 访问本机服务时绕过系统代理（医院常见的代理设置不会截获本机请求）。

停止接口 `POST /service/shutdown` 在所有接口共用的来源校验之外，还要求自定义请求头 `X-Bingli-Control: 1`（普通网页无法满足，会被浏览器的跨域预检拦截）。完整规则见 `docs/LOCAL_SERVICE_SECURITY.md`。

命令行（排查用）：

```powershell
BingliAssistant.exe --status   # 退出码 0 运行中 / 1 未运行 / 2 端口被其他程序占用
BingliAssistant.exe --start    # 无托盘，后台启动并等待就绪
BingliAssistant.exe --stop     # 停止服务
```

构建启动器：`scripts\build_windows_launcher.ps1`（用 Windows 自带的 csc.exe 编译，不需要安装开发工具）。离线包构建脚本会自动把 `BingliAssistant.exe` 放进包里。

## 为什么不直接把服务编译成一个纯二进制

当前语音能力依赖 FunASR、PyTorch、模型文件和 Python 生态。强行把所有内容编译成单个纯二进制，稳定性和可维护性都较差，也容易在医院电脑上遇到动态库、模型缓存和杀毒误报问题。

现阶段更稳的推广包形式是：

```text
一个 setup.exe
└─ 双击后解压完整离线运行环境
   ├─ 创建桌面启动入口
   └─ 引导用户加载 Edge/Chrome 扩展
```

## 包的大小与构建环境

当前离线包约 **2.8 GB（压缩后 / 安装器约 2.0 GB）**：模型约 1.8 GB，Python 依赖约 1.0 GB，Python 运行环境约 70 MB。曾经是 8.1 GB，原因和处理：

- 开发用的 `.venv` 里装了 CUDA 版 torch（数 GB）。现在用 `scripts/build_package_venv.ps1` 在 `build/package-venv` 建一个干净的 **CPU 版**环境来打包；构建脚本发现包里有 CUDA 版 torch 会直接报错；
- Python 运行环境不再整份复制，排除了 `site-packages`、测试、文档、tcl 等；
- 不需要的模型不打包（例如 SenseVoice）。只要批量模型、不要流式识别模型（约 848 MB）时加 `-SkipStreamingModel`，包名带 `-batch`，约 2.0 GB，但听写时没有实时出字，只在停止录音后出结果；
- 生成 `sitecustomize.py`，让 `.venv/Lib/site-packages` 成为真正的站点目录（否则 FunASR 缺少 `distutils`，报 “SeacoParaformer is not registered”），并设置 `PYTHONNOUSERSITE=1`，不读取医生电脑上自己的 Python 包；
- 用 Zip64 的 `ZipFile` 写压缩包（`Compress-Archive` 超过 2 GB 会失败）。

### 启动与“就绪”

服务进程几秒内就能应答，但模型要再加载 30～90 秒（CPU）。启动器让“就绪”等于“模型已加载”：托盘显示“正在加载语音模型…”，`--start` 等到模型加载完才返回（`--model-wait <秒>`，默认 240）；批量模型加载失败时托盘变红并说明原因，流式模型失败则只用批量识别。这样第一次听写不再卡顿：离线包验证里第一次识别由 48 秒降到 2.2 秒（`RTF 0.2`，CPU）。

### 端到端验证

`scripts/smoke_test_package.py <包目录>` 在干净环境里检查：依赖全部来自包内、FunASR 已注册模型类、启动/状态/健康（CPU）、识别并规范化出 `120/80mmHg`、首次识别不卡顿、外来网页被拒绝、停止后不再监听。该脚本**不启动任何浏览器**。完整流程（构建、校验、冒烟、安装器、安装到测试目录并再冒烟）已通过。

## 构建命令

先确保已经生成最新 Windows 离线 ZIP：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_beta_offline_package.ps1 -ProjectRoot E:\project\input
```

然后生成自解压 EXE：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_sfx_installer.ps1 -ProjectRoot E:\project\input
```

## 安装器参数

```powershell
.\bingli-assistant-setup-v0.10.0-beta.exe --info
.\bingli-assistant-setup-v0.10.0-beta.exe --verify-only
.\bingli-assistant-setup-v0.10.0-beta.exe --smoke-test
.\bingli-assistant-setup-v0.10.0-beta.exe --target D:\BingliAssistant --yes --no-launch
```

默认安装目录：

```text
%LOCALAPPDATA%\BingliAssistant
```

## 分发注意事项

- 当前 EXE 未做代码签名，Windows SmartScreen 或医院杀毒软件可能提示风险。
- 对外正式推广前，应使用 Authenticode 代码签名证书签名安装器。
- 医院内网分发时，建议同时提供 SHA256 校验值。
- Edge 插件市场仍只上传浏览器扩展 ZIP；本地服务和模型通过此 EXE 或官网/GitHub Release 分发。

## 当前限制

- 这是内测安装器，**没有卸载器**（卸载需手动删除安装目录和开始菜单里的“病历助手”文件夹）；托盘启动器已提供，但**没有开机自启**；
- 用户仍需要在 Edge/Chrome 的扩展管理页面加载扩展，或等待后续 Edge 商店版本。
- 正式商业版建议继续补：安装目录选择 UI、卸载器、开机自启（可选）、自动更新、代码签名；
- 启动器未经代码签名，同样可能被 SmartScreen 或杀毒软件提示；
- 本机服务对所有接口做来源校验（见 `docs/LOCAL_SERVICE_SECURITY.md`）；默认接受任何浏览器扩展的来源，上架商店、扩展 ID 固定后建议设置 `BINGLI_ALLOWED_EXTENSIONS` 固定扩展。

## 听写窗口（WebView2）与浏览器后备

`BingliAssistant.exe` 用 Microsoft WebView2 控件在自己的窗口里显示服务提供的听写页面（`http://127.0.0.1:8765/app/`），没有地址栏，也不依赖浏览器扩展。

- 需要 WebView2 运行时（Windows 11 自带，多数更新过的 Windows 10 也有）和 exe 旁边的三个文件：`Microsoft.Web.WebView2.Core.dll`、`Microsoft.Web.WebView2.WinForms.dll`、`runtimes\win-x64\native\WebView2Loader.dll`（来自 `packaging/windows/vendor/webview2`，许可证见 `THIRD_PARTY_NOTICES.md`）。
- 任何一项缺失、运行时没装、或窗口启动失败，启动器会记录原因（`launcher.log`），自动改用默认浏览器打开同一个页面。`--browser` 参数可强制使用浏览器。
- 窗口只允许本页面使用麦克风，其他权限一律拒绝；不允许导航到本服务以外的地址，不允许弹出新窗口，关闭了开发者工具和密码保存。窗口的数据放在 `%LOCALAPPDATA%\BingliAssistant\webview`。
- 关闭窗口 = 登录状态消失（令牌只在页面会话里），下一位医生需要重新登录；服务继续运行。
- 要求 64 位 Windows 10 及以上：识别服务（Python 3.12 + PyTorch 2.6）本身就不支持更老的系统。

**没有在真机上验证过窗口本身**（创建窗口会运行 `msedgewebview2.exe`，按约定不在开发机上自动运行）。自动化测试只检查编译、组件齐全、缺少组件时命令行仍可用，以及源码里的安全约束。第一次使用请手工确认：窗口能打开、麦克风可用、登录和听写正常、是否弹出 Windows Hello 的 PIN 提示。
