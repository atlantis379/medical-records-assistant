# 病历助手 v0.10

本项目是一个本地运行的 Chrome/Edge 扩展 + Windows 本地语音识别服务。医生在独立页面中完成语音听写、模板填写、多患者草稿管理和核对，然后复制到医院病历系统。

当前定位：MVP 后的内测/上架准备版本。

## 核心能力

- 本地中文语音识别：FunASR Paraformer；
- 流式输入：边说边显示，失败时自动降级到批量识别；
- 停顿标点：短暂停顿自动逗号，较长停顿自动句号；
- 感染科热词：本地维护，每行一个；
- 模板管理：内置常见病历段落，支持自定义模板；
- 多患者草稿：便于临床场景中临时切换；
- 本地版本历史：手动保存，最多 20 条；
- 可选自动恢复草稿：默认关闭；
- 风险核对提醒：剂量、给药频次、阴阳性、体温/百分比、病原体等；
- TXT 导出和全文复制；
- 插件内测反馈：反馈保存到本机服务，便于小范围试用收集问题。

## 启动方式

1. 双击 `start_server.bat` 启动本地服务；
2. Chrome 打开 `chrome://extensions`，Edge 打开 `edge://extensions`；
3. 开启开发者模式；
4. 选择“加载已解压的扩展”；
5. 选择本项目的 `extension` 文件夹；
6. 如果代码更新过，请在扩展管理页点击“重新加载”。

服务默认只监听 `127.0.0.1:8765`。

## 健康检查

- 服务状态：`http://127.0.0.1:8765/health`
- 授权状态：`http://127.0.0.1:8765/license/status`

当前版本默认返回免费版授权状态，后续可接入 Pro/机构版授权。

## 打包扩展

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_release.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\package_extension.ps1
```

生成的 ZIP 位于 `dist` 目录，可用于 Chrome Web Store / Edge Add-ons 上传前检查。

## 产品化文档

- `docs/PRIVACY_POLICY_DRAFT.md`：隐私政策草案；
- `docs/STORE_LISTING_DRAFT.md`：插件市场文案草案；
- `docs/RELEASE_CHECKLIST.md`：发布检查清单；
- `docs/DISTRIBUTION_PLAN.md`：分发规划；
- `THIRD_PARTY_NOTICES.md`：第三方组件与模型许可说明。

## 隐私原则

- 默认不上传语音和病历草稿到开发者服务器；
- 语音发送到同一台电脑上的本地服务处理；
- 默认不长期保存录音；
- 开启自动恢复后，草稿保存在浏览器本地存储；
- 导出的 TXT 文件由使用者按医院规范自行管理。

## 医疗安全声明

本工具仅用于辅助录入。语音识别可能出现错字、漏字、标点错误、否定词错误、剂量/单位错误和左右侧错误。所有病历内容必须由医生在提交前核对。

## 发布策略

推荐采用“双包分发”：

- 插件市场分发浏览器扩展；
- GitHub Releases 或官网下载页分发 Windows 本地服务和模型安装说明。

不要把完整 Python 环境和大模型直接打入浏览器插件包。

## 内测反馈

插件页面提供“内测反馈”按钮。反馈默认保存到本机：

`server/data/feedback.jsonl`

也可以通过接口导出：

`http://127.0.0.1:8765/feedback/export`

反馈会附带版本、浏览器、服务状态、授权层级等诊断信息，但不会自动附带病历正文。请提醒内测医生不要在反馈正文中填写患者姓名、身份证号、住院号等敏感信息。


## 多语言与英文听写

`v0.8` 增加中英文界面切换和听写语言选择：

- 中文界面 / English UI；
- 中文普通话听写继续使用现有本地 Paraformer，支持流式识别；
- 英文听写走批量识别路径，后端默认尝试 `ASR_MODEL_EN=paraformer-en`；
- 如果英文模型未安装或当前 FunASR 环境无法解析该模型，插件会提示英文模型未就绪，不影响中文听写。

如需指定英文模型，可在启动服务前设置：

```bat
set ASR_MODEL_EN=paraformer-en
start_server.bat
```

上架文案中建议说明：中文听写为当前主能力，英文听写为 Beta 能力，需本地英文模型可用。


## 提交前质控

`v0.9` 增加提交前核对清单：

- 未填写模板占位符提醒：检测 `____`、`___`、`待填`、`待补充` 等未完成内容；
- 药物剂量/频次核对：提示 mg、g、mL、IU、qd、bid、tid、q8h 等剂量和频次；
- 处方动作核对：提示“给予、加用、停用、调整、改为、换用、处方、医嘱、出院带药”等动作，要求核对是否与实际医嘱一致；
- 复制全文前如果仍有高风险项，会先弹出核对清单，但不强制阻止医生复制。

该功能只做提醒，不自动修改病历内容。


## 医用专业词汇包

`v0.10` 增加医用专业词汇包系统：

- `server/data/hotword_packs/general_medical.txt`：通用医学词库；
- `server/data/hotword_packs/infectious_disease.txt`：感染科词库；
- `server/data/hotword_packs/antimicrobials.txt`：抗菌药/抗感染药词库；
- `server/data/hotword_packs/pathogens.txt`：病原体词库；
- `server/data/hotword_packs/user_custom.txt`：用户自定义热词。

插件右侧“热词”页会显示当前启用词库，并支持导入/导出自定义词库。识别时后端会自动合并内置词库和用户自定义热词。

词库来源与维护说明见：

`server/data/hotword_packs/SOURCES.md`

## 离线内测分发包

多数医院电脑无法稳定下载依赖库和模型时，可以使用离线内测包脚本：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_beta_offline_package.ps1 -ProjectRoot E:\project\input
```

默认生成文件夹包：

```text
dist\bingli-assistant-v版本号-beta-offline\
```

该文件夹包含：

- `extension/` 浏览器插件；
- `server/` 本地服务；
- `.venv/` Python 依赖环境；
- `models/modelscope/hub/` 已缓存中文 ASR/VAD 模型；
- `start_server_offline.bat` 离线启动脚本；
- `check_service.bat` 服务检查脚本；
- `README_OFFLINE_BETA.md` 给试用者的安装说明。

如需同时压缩 ZIP，可追加 `-CreateZip`，但 3GB+ 包体压缩会比较慢：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_beta_offline_package.ps1 -ProjectRoot E:\project\input -CreateZip
```

生成后可运行结构检查：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_beta_offline_package.ps1 -PackageRoot E:\project\input\dist\bingli-assistant-v0.10.0-beta-offline
```