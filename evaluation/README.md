# 语音输入评测

回答三个问题：**识别准不准、热词有没有用、数字和单位写得对不对**，并且在**客户电脑的条件（只用 CPU）**下测速度。

评测整条流水线：识别器 → 热词 → 数字单位规范化 → 纠错。线上用的后处理函数（`server/app.py` 的 `postprocess_transcript`）和评测是同一个，所以评测结果就是线上结果。

> 评测默认在 **CPU** 上运行（客户电脑多数没有 GPU）。用 `--device cuda` 会在报告里标明，GPU 上测得的速度不能用于判断。

## 1. 指标

| 指标 | 含义 |
|---|---|
| 内容字错率 | 把数字和单位统一后再比较（一百三十 = 130，毫克 = mg），只反映识别错误 |
| 严格字错率 | 逐字比较，包含格式差异 |
| 识别字错率 | 识别器原始输出与口述原文（`spoken`）比较，不含后处理 |
| 数量+单位 | 数值、单位、空格样式都完全一致（`130/80mmHg`、`30 mg`）；另统计“数值对、只是格式不对”的情况 |
| 否定表述、说反 | `无发热`、`否认高血压`、`阴性/阳性` 是否保留；是否被说反（无→有、阴性→阳性） |
| 字段口令 | 口述的“主诉”“现病史”等字段名是否被识别出来（看识别器原始输出） |
| 药名 / 病原体 / 术语 | 参考文本里出现的词库术语是否出现在结果中 |
| 禁止词 | 样本指定的相似药名等不该出现的词 |
| 数字提示 | 规范化时“无法确定、未转换”的次数 |
| RTF | 推理耗时 ÷ 音频时长，越小越快；1 表示和说话一样快 |

数量、否定、字段口令、术语**不需要手工标注**：从 `reference` 自动提取。小样本时报告同时给出 95% 置信区间，并按 `thresholds.json` 判定：达标需要点估计达标且样本够多；区间上限都够不到目标才判 FAIL；其他是 INCONCLUSIVE（不确定）。**合成语音和仅文本样本永远不会判 PASS。**

## 2. 数据怎么来

1. 打印朗读稿：`python -m evaluation.asr_eval sheet --specialty orthopedics > 朗读稿.md`
   （36 条**虚构**病例，覆盖数字、单位、否定、字段口令、药名；状态：草稿，待医生审核）
2. 医生每人录一遍：每条一个 WAV 文件，文件名是编号（`o01.wav`），16 kHz、单声道。
   - 用平时口述的速度和语气；读错了就重录；
   - **不要加入任何真实患者信息**；文件名和清单里的说话人用化名（`doctor_a`），不要写真名。
3. 生成清单：
   `python -m evaluation.asr_eval init-manifest --audio-dir <录音文件夹> --out <文件夹>/manifest.jsonl --speaker doctor_a`
4. 检查：`python -m evaluation.asr_eval validate --manifest <文件夹>/manifest.jsonl`
   （会检查文件是否存在、采样率、重复编号，并拦截手机号、身份证号、“住院号”等个人信息）

录音和结果属于敏感数据：`evaluation/datasets/**/audio/`、`evaluation/results/` 已加入 `.gitignore`，不要提交。

也可以自己写清单（JSONL，每行一条）：字段见 `evaluation/asr_eval/dataset.py` 文件头。只有识别器原始文本、没有音频时，用 `hypothesis` 字段和 `transcripts` 后端，可以单独评估文本处理部分。

## 3. 运行

```powershell
# 对比几种配置（默认 CPU）
.\.venv\Scripts\python.exe -X utf8 -m evaluation.asr_eval run --manifest evaluation\datasets\<名称>\manifest.jsonl

# 只跑某几个配置
... run --manifest ... --only paraformer_no_hotwords paraformer_specialty

# 改了规范化规则以后，不用重新识别，直接用保存的识别结果重新打分
... rescore --manifest ... --raw evaluation\results\<时间>\raw_outputs.jsonl --only paraformer_specialty
```

模型从 `dist/*/models/modelscope` 自动找（也可用 `--model-cache` 指定）。**不会自动下载任何模型。**

输出在 `evaluation/results/<时间>/`：`summary.md`（总览、阈值判定、各专业/标签分组、错误最多的样本）、`summary.json`、`samples.csv`（Excel 可直接打开）、`raw_outputs.jsonl`（识别器原始输出，可重复打分）。

## 4. 配置（`evaluation/configs/*.json`）

| 配置 | 回答的问题 |
|---|---|
| `paraformer_no_hotwords` | 不用热词的基线 |
| `paraformer_common` | 只用通用词库，热词有没有用 |
| `paraformer_specialty` | 加上所选专业的词库（含草稿），专业词库有没有用 |
| `paraformer_specialty_raw_text` | 不做后处理，规范化修好了多少 |

对比 SenseVoice：`--configs evaluation/configs/sensevoice.example.json`。评测不会自动下载模型；需要先下载一次（约 940 MB），放在评测专用缓存 `~/.cache/modelscope/hub`，不要放进 `dist/` 的安装包目录：

```powershell
.\.venv\Scripts\python.exe -c "import os; from modelscope import snapshot_download; snapshot_download('iic/SenseVoiceSmall', cache_dir=os.path.join(os.path.expanduser('~'), '.cache', 'modelscope', 'hub'))"
```

没有该模型时，对应配置会被跳过并说明原因。

## 5. 阈值

`evaluation/thresholds.json`：内容字错率 ≤ 5%，数量+单位 ≥ 99%，否定表述 ≥ 99%，说反 0 次，字段口令 ≥ 98%，禁止词 0 次，RTF ≤ 1。
**状态：提议值，待项目负责人和医生确认。** 每项至少需要 30 个条目才能判 PASS；99% 这样的目标需要上百个条目才有意义。

## 6. 合成语音（只用于冒烟测试）

```powershell
powershell -ExecutionPolicy Bypass -File evaluation\tools\synthesize_prompts.ps1 -OutDir evaluation\datasets\smoke\audio -Ids r03,r07,i03
python -m evaluation.asr_eval init-manifest --audio-dir evaluation\datasets\smoke\audio --out evaluation\datasets\smoke\manifest.jsonl --speaker synthetic_huihui --source synthetic_tts
```

Windows 自带语音没有口音、停顿和噪声，结果**只能说明流程能跑通**，不能反映真实医生。清单里的 `source` 为 `synthetic_tts`，报告会醒目标注。

## 7. 测试

`tests/test_asr_eval.py` 覆盖指标、提取、清单校验、运行器、阈值判定、报告和命令行，不需要模型或音频。
