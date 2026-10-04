"""Verdicts against thresholds and report files (Markdown, JSON, CSV)."""
import csv
import json
from datetime import datetime
from pathlib import Path

from .metrics import Rate
from .runner import ConfigResult


def pct(value, digits: int = 1) -> str:
    return "-" if value is None else f"{value * 100:.{digits}f}%"


def rate_text(rate: Rate) -> str:
    if rate.total == 0:
        return "-"
    low, high = rate.interval
    return f"{rate.successes}/{rate.total} = {pct(rate.value)} ({pct(low, 0)}-{pct(high, 0)})"


# ----------------------------------------------------------------------------- verdicts
def verdicts(result: ConfigResult, thresholds: dict, device: str = "cpu") -> list[dict]:
    """PASS / FAIL / INCONCLUSIVE per metric. FAIL is certain, PASS needs enough data."""
    summary = result.summary
    min_items = thresholds.get("min_items", 30)
    min_samples = thresholds.get("min_samples", 30)
    reference_samples = sum(1 for s in result.scores if s.cer_content is not None)
    out = []
    for metric, rule in thresholds["metrics"].items():
        label = rule.get("label", metric)
        if metric == "rtf":
            value, n = result.rtf, len(result.rows)
        else:
            value, n = summary.get(metric), None
        if isinstance(value, Rate):
            n = value.total
            if n == 0:
                verdict, shown = "INCONCLUSIVE", "no items"
            else:
                low, high = value.interval
                shown = rate_text(value)
                if "min" in rule and high < rule["min"]:
                    verdict = "FAIL"
                elif "min" in rule and value.value >= rule["min"] and n >= min_items:
                    verdict = "PASS"
                else:
                    verdict = "INCONCLUSIVE"
        elif value is None:
            verdict, shown = "INCONCLUSIVE", "no data"
        else:
            n = reference_samples if metric == "cer_content" else (n if n is not None else len(result.rows))
            shown = f"{value:.4f}" if isinstance(value, float) else str(value)
            if "max" in rule and value > rule["max"]:
                verdict = "FAIL"
            elif "max" in rule and n >= min_samples:
                verdict = "PASS"
            else:
                verdict = "INCONCLUSIVE"
        if metric == "rtf" and device != "cpu" and verdict != "FAIL":
            verdict = "INCONCLUSIVE"
            shown += f"  [measured on {device}; customer computers use the CPU]"
        if verdict == "PASS" and not result.acceptance_valid:
            verdict = "INCONCLUSIVE"
            shown += "  [data not valid for acceptance]"
        target = f"<= {rule['max']}" if "max" in rule else f">= {rule['min']}"
        out.append({"metric": metric, "label": label, "value": shown, "target": target, "n": n, "verdict": verdict})
    return out


# ----------------------------------------------------------------------------- markdown
def _summary_row(name: str, result: ConfigResult) -> list[str]:
    s = result.summary
    return [name, str(s["samples"]), pct(s["cer_content"]), pct(s["cer_strict"]), pct(s["asr_cer"]),
            rate_text(s["quantity_exact"]), rate_text(s["negation_recall"]), str(s["negation_flips"]),
            rate_text(s["field_command"]), rate_text(s["drug"]), rate_text(s["pathogen"]),
            str(s["must_not_hits"]), str(s["number_notices"]), "-" if result.rtf is None else f"{result.rtf:.2f}"]


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def render_markdown(results: dict[str, ConfigResult], thresholds: dict, *, manifest: str, generated: str,
                    environment: dict | None = None) -> str:
    environment = environment or {}
    device = environment.get("device", "cpu")
    done = {n: r for n, r in results.items() if not r.skipped}
    lines = [f"# 语音输入评测报告", "", f"- 生成时间：{generated}", f"- 数据清单：`{manifest}`",
             f"- 阈值状态：{thresholds.get('status', 'n/a')}"]
    if environment:
        lines.append(f"- 运行环境：设备 `{device}`；CPU {environment.get('cpu', '?')}，{environment.get('logical_cores', '?')} 个逻辑核心，"
                     f"torch 线程 {environment.get('torch_threads', '?')}；{environment.get('platform', '')}")
    if device != "cpu":
        lines.append(f"- **本次在 `{device}` 上运行，RTF 不代表客户电脑（多数没有 GPU），不能用于速度结论。**")
    lines.append("")
    sources = {row["source"] for r in done.values() for row in r.rows}
    if sources - {"doctor_recording", "public_corpus"}:
        lines += ["> **注意：数据中包含合成语音或仅文本样本（" + "、".join(sorted(sources - {"doctor_recording", "public_corpus"})) +
                  "）。这类数据只能用来检查流程，不能用于验收或模型取舍。**", ""]
    skipped = {n: r for n, r in results.items() if r.skipped}
    for name, result in skipped.items():
        lines.append(f"- 配置 `{name}` 已跳过：{result.skipped}")
    if skipped:
        lines.append("")

    header = ["配置", "样本", "内容CER", "严格CER", "识别CER", "数量+单位", "否定表述", "说反", "字段口令",
              "药名", "病原体", "禁止词", "数字提示", "RTF"]
    lines += ["## 总览", ""] + _table(header, [_summary_row(n, r) for n, r in done.items()]) + [""]
    lines += ["指标说明：**内容CER**把数字和单位统一后再比较（一百三十=130，毫克=mg），只反映识别错误；**严格CER**逐字比较，"
              "包含格式差异；**识别CER**只比较识别器原始输出与口述原文；**数量+单位**要求数值、单位和空格样式都完全一致；"
              "**说反**指“无发热”被识别成“有发热”等极性错误；括号内为 95% 置信区间。", ""]

    for name, result in done.items():
        lines += [f"## 配置 `{name}`", ""]
        config = result.config
        lines += [f"后端 `{config.backend}`，热词 `{config.hotwords}`，专业 `{config.specialties}`，"
                  f"含草稿词库 `{config.include_draft}`，模式 `{config.profile}`，后处理 `{config.postprocess}`", ""]
        rows = [[v["label"], v["value"], v["target"], v["verdict"]] for v in verdicts(result, thresholds, device)]
        lines += ["### 阈值判定", ""] + _table(["指标", "结果", "目标", "判定"], rows) + [""]
        style = result.summary["digit_style"]
        lines += ["### 识别器原始输出的数字写法", "",
                  f"阿拉伯数字 {style['arabic_numbers']} 个，中文数字 {style['chinese_numbers']} 个；"
                  f"单位符号 {style['unit_symbols']} 个，口语单位 {style['spoken_units']} 个。"
                  "中文数字和口语单位越多，规范化越重要。", ""]
        for title, key in (("按专业", "specialty"), ("按标签", "tag"), ("按数据来源", "source")):
            groups = result.groups.get(key, {})
            if len(groups) > 1 or key == "specialty":
                body = [[n, str(g["samples"]), pct(g["cer_content"]), rate_text(g["quantity_exact"]),
                         rate_text(g["negation_recall"]), rate_text(g["drug"])] for n, g in groups.items()]
                lines += [f"### {title}", ""] + _table(["组", "样本", "内容CER", "数量+单位", "否定表述", "药名"], body) + [""]
        worst = sorted((r for r in result.rows if r["cer_content"] is not None), key=lambda r: r["cer_content"], reverse=True)[:8]
        if worst:
            lines += ["### 错误最多的样本", ""]
            for row in worst:
                lines += [f"- `{row['id']}` 内容CER {pct(row['cer_content'])}", f"  - 应为：{row['reference']}",
                          f"  - 实际：{row['final']}", f"  - 识别原文：{row['raw']}"]
                if row["missed"]:
                    lines.append("  - 未命中：" + "；".join(row["missed"][:8]))
            lines.append("")
        if result.errors:
            lines += ["### 运行中的错误", ""] + [f"- {e}" for e in result.errors[:20]] + [""]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- files
def _plain(value):
    if isinstance(value, Rate):
        low, high = value.interval
        return {"successes": value.successes, "total": value.total, "value": value.value, "ci95": [low, high]}
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def write_reports(results: dict[str, ConfigResult], thresholds: dict, out_dir: Path, *, manifest: str,
                  environment: dict | None = None) -> list[Path]:
    environment = environment or {}
    device = environment.get("device", "cpu")
    out_dir.mkdir(parents=True, exist_ok=True)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    written = []

    md = out_dir / "summary.md"
    md.write_text(render_markdown(results, thresholds, manifest=manifest, generated=generated,
                                            environment=environment), encoding="utf-8")
    written.append(md)

    js = out_dir / "summary.json"
    payload = {"generated": generated, "manifest": manifest, "thresholds_status": thresholds.get("status"),
               "environment": environment, "configs": {}}
    for name, result in results.items():
        payload["configs"][name] = {
            "config": result.config.__dict__, "skipped": result.skipped, "rtf": result.rtf,
            "acceptance_valid": result.acceptance_valid, "errors": result.errors,
            "summary": _plain(result.summary), "groups": _plain(result.groups),
            "verdicts": [] if result.skipped else verdicts(result, thresholds, device),
        }
    js.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(js)

    sheet = out_dir / "samples.csv"
    columns = ["config", "id", "specialty", "source", "speaker", "tags", "spoken", "reference", "raw", "final", "cer_content",
               "cer_strict", "asr_cer", "missed", "format_only", "flipped", "must_not_hits", "notices", "audio_seconds",
               "infer_seconds"]
    with sheet.open("w", encoding="utf-8-sig", newline="") as handle:   # BOM so Excel opens Chinese correctly
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for result in results.values():
            for row in result.rows:
                writer.writerow({k: (";".join(v) if isinstance(v, list) else v) for k, v in row.items()})
    written.append(sheet)
    return written
