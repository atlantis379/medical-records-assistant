# ASR 识别效率、准确度与易用性改进说明

更新时间：2026-08-11

本轮先不更换底层本地模型，重点改进“模型调用层 + 医学上下文 + 用户可见反馈”。原因是内测阶段更需要快速定位：到底是模型能力、热词上下文、录音质量、停顿切分，还是 CPU 性能导致体验不好。

## 参考项目与可借鉴做法

- FunASR Runtime 支持本地/云端实时语音转文字，包含 VAD、非流式 ASR、流式 ASR 和标点模型；其在线 SDK 文档说明 `chunk_size` 会影响流式延迟，并支持 hotword 文件。参考：https://github.com/modelscope/FunASR/blob/main/runtime/docs/SDK_tutorial_online.md
- faster-whisper 在转写接口中提供 VAD、批处理、chunk 长度和 hotwords/hint phrases 等参数，说明语音活动检测和热词提示是提升效率/准确度的常见工程手段。参考：https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py
- whisper.cpp 强调 CPU-only 推理、量化和 VAD；其中 VAD 可减少需要送入模型处理的音频量，从而提升转写速度。参考：https://github.com/ggml-org/whisper.cpp/blob/master/README.md

## 本轮已落地的改进

1. 识别模式分层
   - `fast`：低延迟优先，减少上下文和热词数量。
   - `balanced`：默认模式，兼顾速度、准确度和 CPU 占用。
   - `accurate`：准确优先，增加上下文和医学热词数量，但 CPU 推理会更慢。

2. 热词使用策略
   - 不再把全部词库无差别送入模型。
   - 按“医生自定义热词 > 抗菌药 > 病原体 > 感染科 > 通用医学”排序。
   - 按识别模式限制热词数量和总字符数，避免过长 hotword prompt 拖慢 CPU 推理或干扰识别。

3. 常见医学误识别纠错
   - 新增保守的医学术语纠错规则，例如“梅罗培南 → 美罗培南”“肺炎克雷伯杆菌 → 肺炎克雷伯菌”“pct → PCT”。
   - 仅做术语规范化，不自动改写诊断判断、阴阳性结论或药物医嘱意图。

4. 录音质量诊断
   - 识别前分析 WAV：时长、采样率、声道、音量、峰值、削波比例。
   - 前端显示“音量偏低/偏高、非 16kHz、录音过短、削波爆音”等可操作建议。

5. 性能可观测性
   - 返回并展示 RTF（Real Time Factor）、音频时长、推理耗时、启用热词数、纠错命中数。
   - 新增 `/asr/config` 和 `/asr/performance`，便于内测时记录不同电脑上的 CPU 表现。

## 后续建议

- 内测时记录每次识别的 RTF、录音质量提示和医生手动修改点，优先扩充“医学纠错规则”和“自定义热词”。
- 如果 CPU-only 仍慢，再评估轻量化模型、量化模型或可选高准确模型包。
- 暂不引入方言模型，避免安装包和维护复杂度过快膨胀。
