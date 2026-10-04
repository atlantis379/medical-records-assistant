"""Command line: python -m evaluation.asr_eval <command>

  validate       check a manifest (files, audio format, duplicate ids, personal information)
  run            run configurations over a manifest and write summary.md / summary.json / samples.csv
  rescore        re-score saved recognizer output after changing rules (no audio, no GPU needed)
  init-manifest  build a manifest from the reading prompts and a folder of recordings
  sheet          print the reading sheet the clinicians record from
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "evaluation"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.asr_eval.dataset import Sample, load_manifest, validate  # noqa: E402


def configure_environment(args) -> None:
    """Must run before server.app is imported: it reads these variables at import time."""
    # Customer computers have no GPU, so evaluation runs on the CPU unless a device is asked for explicitly.
    os.environ["ASR_DEVICE"] = getattr(args, "device", None) or os.getenv("ASR_DEVICE") or "cpu"
    if os.environ["ASR_DEVICE"] != "cpu":
        print(f"WARNING: running on {os.environ['ASR_DEVICE']}; speed (RTF) will not represent customer computers.")
    cache = getattr(args, "model_cache", None)
    if not cache and not os.getenv("MODELSCOPE_CACHE"):
        # the same folder the launcher uses: <package>\models\modelscope\hub (the models sit in hub\models\iic)
        found = sorted(ROOT.glob("dist/*/models/modelscope/hub/models/iic"), key=lambda p: p.stat().st_mtime, reverse=True)
        cache = found[0].parents[1] if found else None
    if cache:
        os.environ["MODELSCOPE_CACHE"] = str(cache)
        print(f"model cache: {cache}")
    if not getattr(args, "allow_download", False):
        # a wrong cache folder used to make ModelScope quietly download another copy (about 1 GB) over the network
        for name in ("MODELSCOPE_OFFLINE", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
            os.environ.setdefault(name, "1")


def environment_info() -> dict:
    import platform
    info = {"device": os.environ.get("ASR_DEVICE", "cpu"), "cpu": platform.processor() or platform.machine(),
            "logical_cores": os.cpu_count(), "platform": platform.platform()}
    try:
        import torch
        info["torch_threads"] = torch.get_num_threads()
    except Exception:
        pass
    return info


def print_issues(issues) -> int:
    errors = 0
    for issue in issues:
        print(f"  [{issue.level}] {issue.sample_id}: {issue.message}")
        errors += issue.level == "error"
    return errors


def cmd_validate(args) -> int:
    samples = load_manifest(Path(args.manifest))
    issues = validate(samples, need_audio=not args.no_audio)
    errors = print_issues(issues)
    print(f"{len(samples)} samples, {errors} error(s), {len(issues) - errors} warning(s)")
    return 1 if errors else 0


def load_thresholds(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def run_command(args, *, rescore: bool) -> int:
    configure_environment(args)
    from evaluation.asr_eval import report, runner

    manifest = Path(args.manifest)
    samples = load_manifest(manifest)
    if args.limit:
        samples = samples[: args.limit]
    issues = validate(samples, need_audio=rescore is False and not args.text_only)
    if print_issues(issues) and not args.force:
        print("Fix the errors above (or pass --force).")
        return 1

    configs = runner.load_configs(Path(args.configs))
    if args.only:
        configs = [c for c in configs if c.name in args.only]
        if not configs:
            print(f"no config named {args.only}")
            return 1
    out_dir = Path(args.out) if args.out else EVAL_DIR / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_cache = runner.load_raw_cache(Path(args.raw)) if rescore else None
    raw_out = None if rescore else out_dir / "raw_outputs.jsonl"
    results = runner.run(samples, configs, raw_out=raw_out, raw_cache=raw_cache, warmup=not args.no_warmup,
                         progress=(lambda message: print(message, flush=True)) if not args.quiet else (lambda m: None))
    thresholds = load_thresholds(Path(args.thresholds))
    written = report.write_reports(results, thresholds, out_dir, manifest=str(manifest), environment=environment_info())
    if raw_out:
        written.append(raw_out)
    print("\nWritten:")
    for path in written:
        print(f"  {path}")
    for name, result in results.items():
        if result.skipped:
            print(f"  {name}: SKIPPED - {result.skipped}")
    return 0


def cmd_run(args) -> int:
    return run_command(args, rescore=False)


def cmd_rescore(args) -> int:
    return run_command(args, rescore=True)


def read_prompts(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip() and not line.startswith("#")]


def cmd_init_manifest(args) -> int:
    prompts = read_prompts(Path(args.prompts))
    audio_dir = Path(args.audio_dir)
    out = Path(args.out)
    lines, missing = [], []
    for prompt in prompts:
        audio = audio_dir / f"{prompt['id']}.wav"
        if not audio.exists():
            missing.append(prompt["id"])
            continue
        try:
            relative = os.path.relpath(audio, out.parent).replace("\\", "/")
        except ValueError:      # different drive on Windows
            relative = str(audio)
        entry = {"id": prompt["id"], "audio": relative, "specialty": prompt["specialty"], "speaker": args.speaker,
                 "source": args.source, "spoken": prompt["spoken"], "reference": prompt["reference"],
                 "tags": prompt.get("tags", [])}
        for key in ("entities", "must_not"):
            if key in prompt:
                entry[key] = prompt[key]
        lines.append(json.dumps(entry, ensure_ascii=False))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(lines)} samples written to {out}")
    if missing:
        print(f"{len(missing)} prompt(s) have no recording yet: {', '.join(missing[:10])}{' ...' if len(missing) > 10 else ''}")
    return 0


def cmd_sheet(args) -> int:
    prompts = read_prompts(Path(args.prompts))
    if args.specialty:
        prompts = [p for p in prompts if p["specialty"] == args.specialty]
    lines = ["# 朗读稿", "",
             "请用平时口述病历的速度和语气朗读，每条录成一个文件，文件名为“编号.wav”（16 kHz、单声道）。",
             "稿中的病例均为虚构，**朗读时不要加入任何真实患者信息**。读错了就重录这一条。", ""]
    current = None
    for prompt in prompts:
        if prompt["specialty"] != current:
            current = prompt["specialty"]
            lines += [f"## {current}", ""]
        lines += [f"**{prompt['id']}**　{prompt['spoken']}", ""]
    print("\n".join(lines))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evaluation.asr_eval", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_run_options(p, *, rescore: bool):
        p.add_argument("--manifest", required=True)
        p.add_argument("--configs", default=str(EVAL_DIR / "configs" / "default.json"))
        p.add_argument("--thresholds", default=str(EVAL_DIR / "thresholds.json"))
        p.add_argument("--out", help="output folder (default evaluation/results/<timestamp>)")
        p.add_argument("--only", nargs="*", help="run only these config names")
        p.add_argument("--limit", type=int, help="use the first N samples")
        p.add_argument("--device", choices=["cpu", "cuda"], help="recognizer device; default cpu (customer computers have no GPU)")
        p.add_argument("--model-cache", help="ModelScope cache folder (default: newest dist/*/models/modelscope)")
        p.add_argument("--no-warmup", action="store_true", help="count model start-up in RTF")
        p.add_argument("--allow-download", action="store_true", help="let ModelScope download missing models (off by default)")
        p.add_argument("--text-only", action="store_true", help="manifest has hypotheses instead of audio")
        p.add_argument("--force", action="store_true", help="continue despite manifest errors")
        p.add_argument("--quiet", action="store_true")
        if rescore:
            p.add_argument("--raw", required=True, help="raw_outputs.jsonl from an earlier run")

    p = sub.add_parser("validate", help=cmd_validate.__doc__)
    p.add_argument("--manifest", required=True)
    p.add_argument("--no-audio", action="store_true", help="do not check audio files")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("run")
    add_run_options(p, rescore=False)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("rescore")
    add_run_options(p, rescore=True)
    p.set_defaults(func=cmd_rescore)

    p = sub.add_parser("init-manifest")
    p.add_argument("--prompts", default=str(EVAL_DIR / "prompts" / "prompts.jsonl"))
    p.add_argument("--audio-dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--speaker", default="doctor_a", help="pseudonym, never a real name")
    p.add_argument("--source", default="doctor_recording", choices=["doctor_recording", "synthetic_tts", "public_corpus"])
    p.set_defaults(func=cmd_init_manifest)

    p = sub.add_parser("sheet")
    p.add_argument("--prompts", default=str(EVAL_DIR / "prompts" / "prompts.jsonl"))
    p.add_argument("--specialty")
    p.set_defaults(func=cmd_sheet)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
