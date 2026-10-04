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

**安装包用户**：双击桌面“病历助手”图标（没有黑色窗口，托盘小圆点显示状态），用完在听写页面右上角点“停止服务”。详见 `docs/WINDOWS_EXECUTABLE_DISTRIBUTION.md`。

**开发者**：

1. 双击 `start_server.bat` 启动本地服务（只会停止自己的旧服务，端口被其他程序占用时只提示，不会结束它）；
2. Chrome 打开 `chrome://extensions`，Edge 打开 `edge://extensions`；
3. 开启开发者模式；
4. 选择“加载已解压的扩展”；
5. 选择本项目的 `extension` 文件夹；
6. 如果代码更新过，请在扩展管理页点击“重新加载”。

服务默认只监听 `127.0.0.1:8765`，并且只接受浏览器扩展和服务自己状态页的请求：电脑上打开的其他网页无法调用它（详见 `docs/LOCAL_SERVICE_SECURITY.md`）。

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

## 专业词库

热词按医生在本机选择的专业启用（右侧“热词”页 →“我的专业”，可多选，设置只保存在本机）。

- 首批专业：感染科、呼吸与危重症科、骨科；
- 通用词库（通用医学、临床指标、既往史、用户自定义）对所有专业启用；
- 专业词库由 `server/data/hotword_packs/<id>.manifest.json` 描述（专业、版本、来源、审核人、状态）；
- **未经医生审核（状态 `draft`）的词库默认不启用**，需在“我的专业”中勾选“试用尚未经医生审核的专业词库”；
- 呼吸与危重症科、骨科词库目前均为草稿，等待对应专业医生审核；
- 未选择专业时，服务端默认使用感染科（可用环境变量 `APP_SPECIALTIES` 修改）；
- 纠错规则可通过 `specialties` 字段限定适用专业，未标注的规则对所有专业生效。

词库准入与来源规则见 `docs/DATA_SOURCE_POLICY.md`。

## 按字段归位

听写时说出字段名，内容会自动写到对应的标题下（编辑框上方有字段栏，可以关闭）：

- 说“主诉，反复咳嗽三年。现病史，患者三年前……”，内容分别进入 `主诉：`、`现病史：`；
- 字段名只在**一句话开头或停顿之后**才算口令，句子中间出现的“主诉”“既往史”不会切换字段；
- “诊断”这类常见词只有后面跟着逗号或冒号时才算口令；
- 没说字段名时，内容接着写入“当前字段”；也可以点字段栏选字段，或者把光标放进某个字段；
- 没有当前字段、也没说字段名时，保持原来的插入方式，不会丢内容；
- 字段按专业显示（感染科有“流行病学史”，骨科有“专科检查”）；
- 核对栏会提示：字段标题下没有内容、有内容放在所有字段标题之前（未归入任何字段）。

草稿仍是带标题的纯文本（`主诉：…`），复制、导出、版本记录都和以前一样。口令词和同音别名在 `extension/fields.js`，别名是暂定的，需要用评测里的“字段口令”指标逐步补充。

## 数字与单位

口述的数字和单位会按 `docs/CLINICAL_NUMBER_UNIT_RULES.md` 规范化：`一百三十八十毫米汞柱` → `130/80mmHg`，`三十毫克每日三次` → `30 mg tid`。

- `℃`、`%`、`次/分`、`mmHg`、`°` 紧贴数字，其余单位与数字之间空一格；
- 只转换“数字后紧跟已知单位”或“已知指标名后的数字”；
- 有歧义或无法确定的数字**不转换**，保留原话，并在右侧“核对”栏提示，请医生手动改；
- 单位表和指标名表在 `server/data/normalization/`，新增单位只需改数据文件。

## 语音输入评测

`evaluation/` 提供离线评测工具：字错率、数量+单位、否定表述、药名、字段口令、速度（RTF），并对比“有无热词”“有无后处理”等配置。**评测一律在 CPU 上运行**（客户电脑多数没有 GPU）。

```powershell
python -m evaluation.asr_eval sheet --specialty orthopedics      # 打印给医生的朗读稿（虚构病例）
python -m evaluation.asr_eval validate --manifest <清单>          # 检查录音和清单
python -m evaluation.asr_eval run --manifest <清单>               # 运行并生成报告
```

详见 `evaluation/README.md`。录音和结果不要提交到 git（已在 `.gitignore` 中排除）。

## 回归测试

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -t .
```

发布检查脚本 `scripts/check_release.ps1` 会自动运行这些测试。

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