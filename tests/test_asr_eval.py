"""Tests for the evaluation tooling (evaluation/asr_eval). No model or audio is needed."""
import io
import json
import math
import os
import struct
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from evaluation.asr_eval import backends, dataset, metrics, report, runner
from evaluation.asr_eval.__main__ import main
from evaluation.asr_eval.backends import Backend, BackendUnavailable, RawResult

ROOT = Path(__file__).resolve().parents[1]


class EditDistance(unittest.TestCase):
    def test_known_distances(self):
        self.assertEqual(metrics.edit_operations("abc", "abc")[0], 0)
        self.assertEqual(metrics.edit_operations("abc", "abd"), (1, 1, 0, 0))
        self.assertEqual(metrics.edit_operations("abc", "ab"), (1, 0, 1, 0))
        self.assertEqual(metrics.edit_operations("ab", "abc"), (1, 0, 0, 1))
        self.assertEqual(metrics.edit_operations("", "abc"), (3, 0, 0, 3))
        self.assertEqual(metrics.edit_operations("kitten", "sitting")[0], 3)

    def test_breakdown_adds_up(self):
        edits, subs, dels, inss = metrics.edit_operations("无发热无胸痛", "有发热胸痛咳")
        self.assertEqual(edits, subs + dels + inss)

    def test_cer_ignores_punctuation_and_spaces(self):
        self.assertEqual(metrics.cer("无发热，无胸痛。", "无发热 无胸痛", canonical=False).edits, 0)

    def test_content_cer_treats_spoken_and_written_numbers_alike(self):
        self.assertEqual(metrics.cer("血压一百三十毫米汞柱", "血压130mmHg", canonical=True).edits, 0)
        self.assertEqual(metrics.cer("三十毫克", "30 mg", canonical=True).edits, 0)
        self.assertEqual(metrics.cer("百分之九十二", "92%", canonical=True).edits, 0)
        self.assertGreater(metrics.cer("血压一百三十毫米汞柱", "血压130mmHg", canonical=False).edits, 0)

    def test_content_cer_still_sees_real_errors(self):
        self.assertGreater(metrics.cer("三十毫克", "三十毫升", canonical=True).edits, 0)
        self.assertGreater(metrics.cer("无发热", "有发热", canonical=True).edits, 0)


class SpacedRecognizerOutput(unittest.TestCase):
    """Paraformer returns '左 膝 关 节'. Without cleaning, numerals and field names were not recognised."""
    spaced = "主 诉 咳 嗽 一 百 二 十 度"

    def test_collapse_keeps_spaces_between_latin_tokens(self):
        self.assertEqual(metrics.collapse_spaces(self.spaced), "主诉咳嗽一百二十度")
        self.assertEqual(metrics.collapse_spaces("PaO2 60 mmHg"), "PaO2 60 mmHg")
        self.assertEqual(metrics.collapse_spaces("血氧 PaO2 60 mmHg 正常"), "血氧PaO2 60 mmHg正常")

    def test_numerals_in_spaced_text_are_canonicalised(self):
        self.assertEqual(metrics.canonicalize(self.spaced), metrics.canonicalize("主诉咳嗽120度"))
        self.assertEqual(metrics.cer("主诉咳嗽一百二十度", self.spaced, canonical=True).edits, 0)

    def test_field_command_and_digit_style_see_through_the_spaces(self):
        score = metrics.score_sample("s", reference="主诉：咳嗽。", spoken="主诉咳嗽", raw_text="主 诉 咳 嗽",
                                     final_text="主诉：咳嗽。", lexicon=metrics.Lexicon.load())
        self.assertTrue([e for e in score.entities if e.type == "field_command"][0].exact)
        style = metrics.digit_style(metrics.collapse_spaces(self.spaced))
        self.assertEqual(style["chinese_numbers"], 1)


class Extraction(unittest.TestCase):
    def test_quantities(self):
        text = "血压130/80mmHg，体温36.8℃，PEEP 5 cmH2O，吸入氧浓度 50%，白细胞 12.3×10^9/L，屈曲120°，肿块 3×2 cm，美罗培南 1 g"
        found = metrics.extract_quantities(text)
        for expected in ["130/80mmHg", "36.8℃", "5 cmH2O", "50%", "12.3×10^9/L", "120°", "3×2 cm", "1 g"]:
            self.assertIn(expected, found)

    def test_quantity_range(self):
        self.assertIn("130-140/80-90mmHg", metrics.extract_quantities("血压130-140/80-90mmHg"))

    def test_negations_and_polarity(self):
        entities = metrics.extract_negations("患者无发热，否认高血压病史，未闻及干啰音，浮髌试验阴性")
        found = {e["phrase"] for e in entities}
        # a phrase is the marker plus up to four following characters
        self.assertIn("无发热", found)
        self.assertIn("未闻及干啰音", found)
        self.assertTrue(any(p.startswith("否认高血压") for p in found))
        self.assertTrue(any(e.get("polarity") and e["phrase"].endswith("阴性") for e in entities))
        self.assertTrue(any(e.get("polarity") for e in metrics.extract_negations("叩击痛阳性")))

    def test_flipped_polarity_is_detected(self):
        entity = metrics.extract_negations("患者无发热")[0]
        self.assertTrue(metrics.polarity_flipped(entity, "患者有发热"))
        self.assertFalse(metrics.polarity_flipped(entity, "患者无发热"))
        self.assertFalse(metrics.polarity_flipped(entity, "患者发热"))   # lost, but not reversed
        positive = metrics.extract_negations("试验阴性")[0]
        self.assertTrue(metrics.polarity_flipped(positive, "试验阳性"))

    def test_field_labels(self):
        labels = metrics.extract_field_labels("主诉：咳嗽。\n现病史：三天。")
        self.assertEqual(set(labels), {"主诉", "现病史"})
        self.assertEqual(metrics.extract_field_labels("既往体健"), [])

    def test_lexicon_categories(self):
        lexicon = metrics.Lexicon.load()
        terms = dict(lexicon.terms_in("予美罗培南治疗肺炎克雷伯菌感染，塞来昔布口服，股骨颈骨折"))
        self.assertEqual(terms.get("美罗培南"), "drug")
        self.assertEqual(terms.get("肺炎克雷伯菌"), "pathogen")
        self.assertEqual(terms.get("塞来昔布"), "drug")      # from the 药物 section of the orthopedics pack
        self.assertEqual(terms.get("股骨颈骨折"), "term")

    def test_lexicon_keeps_only_the_longest_match(self):
        lexicon = metrics.Lexicon(words={"骨折": "term", "股骨颈骨折": "term"})
        self.assertEqual([w for w, _ in lexicon.terms_in("股骨颈骨折")], ["股骨颈骨折"])


class SampleScoring(unittest.TestCase):
    def score(self, reference, final, raw=None, **kwargs):
        return metrics.score_sample("s1", reference=reference, spoken=None, raw_text=raw if raw is not None else final,
                                    final_text=final, lexicon=metrics.Lexicon.load(), **kwargs)

    def by_type(self, score, entity_type):
        return [e for e in score.entities if e.type == entity_type]

    def test_perfect_sample(self):
        score = self.score("血压130/80mmHg，无发热。", "血压130/80mmHg，无发热。")
        self.assertEqual(score.cer_strict.edits, 0)
        self.assertTrue(all(e.exact for e in score.entities))

    def test_format_only_error_is_not_a_value_error(self):
        score = self.score("血压130/80mmHg", "血压130/80 mmHg")
        quantity = self.by_type(score, "quantity")[0]
        self.assertFalse(quantity.exact)
        self.assertTrue(quantity.value_ok)
        self.assertEqual(score.cer_content.edits, 0)       # content is right
        self.assertEqual(score.cer_strict.edits, 0)        # spaces are ignored by CER; the quantity metric catches style

    def test_wrong_value_is_caught(self):
        score = self.score("体温36.8℃", "体温38.6℃")
        quantity = self.by_type(score, "quantity")[0]
        self.assertFalse(quantity.exact or quantity.value_ok)

    def test_spoken_numbers_left_unconverted_count_against_the_quantity_metric(self):
        score = self.score("血压130/80mmHg", "血压一百三十八十mmHg")
        self.assertFalse(self.by_type(score, "quantity")[0].exact)

    def test_negation_loss_and_flip(self):
        lost = self.score("患者无发热", "患者发热")
        self.assertFalse(self.by_type(lost, "negation")[0].exact)
        self.assertFalse(self.by_type(lost, "negation")[0].flipped)
        flipped = self.score("患者无发热", "患者有发热")
        self.assertTrue(self.by_type(flipped, "negation")[0].flipped)

    def test_field_command_is_judged_on_the_raw_text(self):
        score = self.score("主诉：咳嗽。", "主诉：咳嗽。", raw="咳嗽")
        self.assertFalse(self.by_type(score, "field_command")[0].exact)
        score = self.score("主诉：咳嗽。", "咳嗽。", raw="主诉咳嗽")
        self.assertTrue(self.by_type(score, "field_command")[0].exact)

    def test_look_alike_drug_is_flagged(self):
        score = self.score("予美罗培南", "予亚胺培南", must_not=["亚胺培南"])
        self.assertEqual(score.must_not_hits, ["亚胺培南"])
        self.assertFalse(self.by_type(score, "drug")[0].exact)

    def test_sample_without_reference_only_gets_digit_statistics(self):
        score = metrics.score_sample("s2", reference=None, spoken=None, raw_text="血压一百三十毫米汞柱，心率90次/分",
                                     final_text="x")
        self.assertIsNone(score.cer_content)
        self.assertEqual(score.digit_style["arabic_numbers"], 1)
        self.assertEqual(score.digit_style["chinese_numbers"], 1)
        self.assertEqual(score.digit_style["spoken_units"], 1)


class Statistics(unittest.TestCase):
    def test_wilson_interval(self):
        low, high = metrics.wilson_interval(50, 100)
        self.assertAlmostEqual(low, 0.404, places=2)
        self.assertAlmostEqual(high, 0.596, places=2)
        low, high = metrics.wilson_interval(100, 100)
        self.assertGreater(low, 0.96)
        self.assertAlmostEqual(high, 1.0)
        self.assertEqual(metrics.wilson_interval(0, 0), (0.0, 1.0))

    def test_micro_average_weighs_long_samples_more(self):
        short = metrics.CerResult(edits=1, reference_length=2)     # 50 %
        long = metrics.CerResult(edits=1, reference_length=98)     # ~1 %
        self.assertAlmostEqual(metrics.micro_cer([short, long]), 0.02)


class DatasetChecks(unittest.TestCase):
    def write(self, tmp, lines):
        path = Path(tmp) / "manifest.jsonl"
        path.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines), encoding="utf-8")
        return path

    def test_loads_and_resolves_audio_relative_to_the_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, [{"id": "a", "audio": "wav/a.wav", "reference": "x"}])
            sample = dataset.load_manifest(path)[0]
            self.assertEqual(sample.audio, Path(tmp) / "wav" / "a.wav")

    def test_rejects_bad_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.jsonl"
            path.write_text("{not json}", encoding="utf-8")
            with self.assertRaises(ValueError):
                dataset.load_manifest(path)
            path.write_text('{"reference": "x"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                dataset.load_manifest(path)

    def test_validation_catches_common_mistakes(self):
        samples = [
            dataset.Sample(id="a", hypothesis="x", reference="y"),
            dataset.Sample(id="a", hypothesis="x", reference="y"),                                  # duplicate
            dataset.Sample(id="b", hypothesis="x", reference="y", source="made_up"),                # unknown source
            dataset.Sample(id="c", audio=Path("missing.wav"), reference="y"),                       # no such file
            dataset.Sample(id="d", hypothesis="x", reference="电话13812345678"),                    # phone number
            dataset.Sample(id="e", hypothesis="x", reference="住院号12345"),                        # identifier word
            dataset.Sample(id="f", hypothesis="x", reference="y", speaker="张医生"),                # real name
        ]
        messages = " | ".join(f"{i.level}:{i.sample_id}:{i.message}" for i in dataset.validate(samples))
        for fragment in ["error:a:duplicate id", "error:b:unknown source", "error:c:audio file not found",
                         "error:d:reference looks like a phone number", "error:e:reference mentions an identifier",
                         "warning:f:speaker looks like a real name"]:
            self.assertIn(fragment, messages)

    def test_wav_format_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.wav"
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(44100)
                wf.writeframes(struct.pack("<h", 0) * 2 * 44100)
            issues = dataset.validate([dataset.Sample(id="a", audio=path, reference="y")])
            self.assertTrue(any(i.level == "warning" and "16 kHz mono" in i.message for i in issues))
            self.assertFalse(any(i.level == "error" for i in issues))


SAMPLES = [
    dataset.Sample(id="s1", specialty="orthopedics", source="doctor_recording", hypothesis="左膝关节屈曲一百二十度伸直零度",
                   reference="左膝关节屈曲120°，伸直0°。", tags=["rom"]),
    dataset.Sample(id="s2", specialty="infectious_disease", source="doctor_recording", hypothesis="患者有发热",
                   reference="患者无发热。", tags=["negation"]),
]


class FakeBackend(Backend):
    name = "fake"
    calls = 0

    def transcribe(self, sample, hotword, profile):
        FakeBackend.calls += 1
        return RawResult(sample.hypothesis, 2.0, 1.0, len(hotword.split()))


class BrokenBackend(Backend):
    name = "broken"

    def prepare(self):
        raise BackendUnavailable("model missing")


class Runner(unittest.TestCase):
    def configs(self, **extra):
        return [runner.Config(name="fake", backend="fake", **extra)]

    def run_with(self, configs, **kwargs):
        with mock.patch.dict(runner.BACKENDS, {"fake": FakeBackend, "broken": BrokenBackend}):
            return runner.run(SAMPLES, configs, **kwargs)

    def test_text_pipeline_is_applied_and_scored(self):
        result = self.run_with(self.configs())["fake"]
        self.assertEqual(result.rows[0]["final"], "左膝关节屈曲120°伸直0°。")
        self.assertEqual(result.summary["samples"], 2)
        self.assertEqual(result.summary["quantity_exact"].value, 1.0)       # both quantities converted
        self.assertEqual(result.summary["negation_flips"], 1)               # 无发热 -> 有发热 in sample two
        self.assertAlmostEqual(result.rtf, 0.5)

    def test_postprocess_off_shows_what_normalisation_fixes(self):
        result = self.run_with(self.configs(postprocess=False))["fake"]
        self.assertEqual(result.summary["quantity_exact"].value, 0.0)

    def test_groups_by_specialty_source_and_tag(self):
        result = self.run_with(self.configs())["fake"]
        self.assertEqual(set(result.groups["specialty"]), {"orthopedics", "infectious_disease"})
        self.assertEqual(set(result.groups["source"]), {"doctor_recording"})
        self.assertIn("rom", result.groups["tag"])

    def test_unavailable_backend_is_skipped_not_fatal(self):
        results = self.run_with([runner.Config(name="b", backend="broken"), runner.Config(name="fake", backend="fake")])
        self.assertIn("model missing", results["b"].skipped)
        self.assertIsNone(results["fake"].skipped)

    def test_one_failing_sample_does_not_stop_the_run(self):
        class Flaky(FakeBackend):
            def transcribe(self, sample, hotword, profile):
                if sample.id == "s1":
                    raise RuntimeError("boom")
                return super().transcribe(sample, hotword, profile)

        with mock.patch.dict(runner.BACKENDS, {"fake": Flaky}):
            result = runner.run(SAMPLES, self.configs())["fake"]
        self.assertEqual(len(result.rows), 1)
        self.assertIn("s1", result.errors[0])

    def test_hotword_modes_and_specialty_selection(self):
        from server import app as srv
        sample = dataset.Sample(id="x", specialty="orthopedics")
        off = runner.hotword_string(srv, runner.Config(name="a", hotwords="off"), sample)
        common = runner.hotword_string(srv, runner.Config(name="b", hotwords="common", profile="accurate"), sample)
        with_draft = runner.hotword_string(srv, runner.Config(name="c", hotwords="specialty", include_draft=True, profile="accurate"), sample)
        without_draft = runner.hotword_string(srv, runner.Config(name="d", hotwords="specialty", include_draft=False, profile="accurate"), sample)
        self.assertEqual(off, "")
        self.assertNotIn("股骨颈骨折", common.split())
        self.assertIn("股骨颈骨折", with_draft.split())
        self.assertNotIn("股骨颈骨折", without_draft.split())

    def test_raw_output_can_be_rescored_without_the_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = Path(tmp) / "raw.jsonl"
            first = self.run_with(self.configs(), raw_out=raw_path)["fake"]
            cache = runner.load_raw_cache(raw_path)
            FakeBackend.calls = 0
            with mock.patch.dict(runner.BACKENDS, {"fake": BrokenBackend}):   # would be skipped if it were needed
                second = runner.run(SAMPLES, self.configs(), raw_cache=cache)["fake"]
            self.assertIsNone(second.skipped)
            self.assertEqual([r["final"] for r in first.rows], [r["final"] for r in second.rows])

    def test_config_file_is_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps({"configs": [{"name": "x", "backend": "nope"}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                runner.load_configs(path)
            path.write_text(json.dumps({"configs": [{"name": "x", "hotwords": "maybe"}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                runner.load_configs(path)
            path.write_text(json.dumps({"configs": [{"name": "x"}, {"name": "x"}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                runner.load_configs(path)

    def test_shipped_configs_are_valid(self):
        for name in ("default.json", "sensevoice.example.json", "text_only.json"):
            self.assertTrue(runner.load_configs(ROOT / "evaluation" / "configs" / name), name)


class Verdicts(unittest.TestCase):
    thresholds = json.loads((ROOT / "evaluation" / "thresholds.json").read_text(encoding="utf-8"))

    def make_result(self, successes, total, *, valid=True):
        result = runner.ConfigResult(runner.Config(name="x"))
        result.scores = [metrics.SampleScore("s", cer_content=metrics.CerResult(0, 10))] * 40
        result.rows = [{"source": "doctor_recording"}] * 40
        result.summary = {"cer_content": 0.02, "quantity_exact": metrics.Rate(successes, total),
                          "negation_recall": metrics.Rate(total, total), "negation_flips": 0,
                          "field_command": metrics.Rate(total, total), "must_not_hits": 0}
        result.rtf = 0.5
        result.acceptance_valid = valid
        return result

    def verdict(self, result, metric):
        return next(v["verdict"] for v in report.verdicts(result, self.thresholds) if v["metric"] == metric)

    def test_pass_needs_the_target_and_enough_items(self):
        self.assertEqual(self.verdict(self.make_result(100, 100), "quantity_exact"), "PASS")
        self.assertEqual(self.verdict(self.make_result(10, 10), "quantity_exact"), "INCONCLUSIVE")   # too few

    def test_fail_when_even_the_optimistic_bound_misses(self):
        self.assertEqual(self.verdict(self.make_result(50, 100), "quantity_exact"), "FAIL")

    def test_close_to_target_with_little_data_is_inconclusive(self):
        self.assertEqual(self.verdict(self.make_result(98, 100), "quantity_exact"), "INCONCLUSIVE")
        self.assertEqual(self.verdict(self.make_result(97, 100), "quantity_exact"), "FAIL")   # upper bound 98.97% < 99%

    def test_synthetic_or_text_only_data_can_never_pass(self):
        self.assertEqual(self.verdict(self.make_result(100, 100, valid=False), "quantity_exact"), "INCONCLUSIVE")
        self.assertEqual(self.verdict(self.make_result(100, 100, valid=False), "cer_content"), "INCONCLUSIVE")

    def test_limits_fail_immediately(self):
        result = self.make_result(100, 100)
        result.summary["negation_flips"] = 1
        self.assertEqual(self.verdict(result, "negation_flips"), "FAIL")
        result.rtf = 1.5
        self.assertEqual(self.verdict(result, "rtf"), "FAIL")

    def test_speed_measured_off_the_cpu_is_never_a_pass(self):
        result = self.make_result(100, 100)
        cpu = next(v for v in report.verdicts(result, self.thresholds, "cpu") if v["metric"] == "rtf")
        gpu = next(v for v in report.verdicts(result, self.thresholds, "cuda") if v["metric"] == "rtf")
        self.assertEqual(cpu["verdict"], "PASS")
        self.assertEqual(gpu["verdict"], "INCONCLUSIVE")
        self.assertIn("customer computers use the CPU", gpu["value"])

    def test_thresholds_are_labelled_as_proposed(self):
        self.assertIn("proposed", self.thresholds["status"])


class Reports(unittest.TestCase):
    def test_report_files_are_written(self):
        with mock.patch.dict(runner.BACKENDS, {"fake": FakeBackend}):
            results = runner.run(SAMPLES, [runner.Config(name="fake", backend="fake")])
        thresholds = json.loads((ROOT / "evaluation" / "thresholds.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            written = report.write_reports(results, thresholds, Path(tmp), manifest="m.jsonl")
            self.assertEqual({p.name for p in written}, {"summary.md", "summary.json", "samples.csv"})
            markdown = (Path(tmp) / "summary.md").read_text(encoding="utf-8")
            self.assertIn("总览", markdown)
            self.assertIn("fake", markdown)
            payload = json.loads((Path(tmp) / "summary.json").read_text(encoding="utf-8"))
            self.assertIn("quantity_exact", payload["configs"]["fake"]["summary"])
            csv_text = (Path(tmp) / "samples.csv").read_bytes()
            self.assertTrue(csv_text.startswith(b"\xef\xbb\xbf"))      # Excel-friendly

    def test_synthetic_data_is_flagged_in_the_report(self):
        samples = [dataset.Sample(id="t", source="synthetic_tts", hypothesis="无发热", reference="无发热。")]
        with mock.patch.dict(runner.BACKENDS, {"fake": FakeBackend}):
            results = runner.run(samples, [runner.Config(name="fake", backend="fake")])
        thresholds = json.loads((ROOT / "evaluation" / "thresholds.json").read_text(encoding="utf-8"))
        text = report.render_markdown(results, thresholds, manifest="m", generated="now")
        self.assertIn("不能用于验收", text)


class CpuDefault(unittest.TestCase):
    def test_evaluation_runs_on_the_cpu_unless_told_otherwise(self):
        from evaluation.asr_eval.__main__ import build_parser, configure_environment
        args = build_parser().parse_args(["run", "--manifest", "m"])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ASR_DEVICE", None)
            configure_environment(args)
            self.assertEqual(os.environ["ASR_DEVICE"], "cpu")

    def test_gpu_has_to_be_requested_explicitly_and_is_flagged_in_the_report(self):
        results = {}
        thresholds = json.loads((ROOT / "evaluation" / "thresholds.json").read_text(encoding="utf-8"))
        text = report.render_markdown(results, thresholds, manifest="m", generated="now", environment={"device": "cuda"})
        self.assertIn("不代表客户电脑", text)


class CommandLine(unittest.TestCase):
    def run_cli(self, *argv):
        buffer = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=False), redirect_stdout(buffer):
            code = main(list(argv))
        return code, buffer.getvalue()

    def text_manifest(self, tmp):
        path = Path(tmp) / "m.jsonl"
        path.write_text("\n".join(json.dumps(
            {"id": f"s{i}", "specialty": "orthopedics", "source": "transcript_only", "hypothesis": "屈曲一百二十度",
             "reference": "屈曲120°。"}, ensure_ascii=False) for i in range(2)), encoding="utf-8")
        return path

    def test_run_text_only_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            code, text = self.run_cli("run", "--manifest", str(self.text_manifest(tmp)), "--configs",
                                      str(ROOT / "evaluation" / "configs" / "text_only.json"), "--out", str(out),
                                      "--text-only", "--quiet", "--no-warmup")
            self.assertEqual(code, 0, text)
            self.assertTrue((out / "summary.md").exists())
            payload = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            raw = payload["configs"]["raw_text"]["summary"]["quantity_exact"]["value"]
            fixed = payload["configs"]["with_postprocessing"]["summary"]["quantity_exact"]["value"]
            self.assertEqual((raw, fixed), (0.0, 1.0))

    def test_rescore_reproduces_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.text_manifest(tmp)
            raw = Path(tmp) / "raw.jsonl"
            raw.write_text("\n".join(json.dumps({"config": "with_postprocessing", "sample_id": f"s{i}", "raw_text": "屈曲一百二十度",
                                                 "audio_seconds": 1.0, "infer_seconds": 0.1}, ensure_ascii=False) for i in range(2)),
                           encoding="utf-8")
            out = Path(tmp) / "out"
            code, _ = self.run_cli("rescore", "--manifest", str(manifest), "--configs",
                                   str(ROOT / "evaluation" / "configs" / "text_only.json"), "--only", "with_postprocessing",
                                   "--raw", str(raw), "--out", str(out), "--text-only", "--quiet")
            self.assertEqual(code, 0)
            payload = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["configs"]["with_postprocessing"]["summary"]["quantity_exact"]["value"], 1.0)

    def test_validate_reports_errors_with_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.jsonl"
            path.write_text(json.dumps({"id": "a", "audio": "nope.wav", "reference": "x"}), encoding="utf-8")
            code, text = self.run_cli("validate", "--manifest", str(path))
            self.assertEqual(code, 1)
            self.assertIn("audio file not found", text)

    def test_sheet_and_init_manifest(self):
        code, text = self.run_cli("sheet", "--specialty", "orthopedics")
        self.assertEqual(code, 0)
        self.assertIn("o01", text)
        self.assertNotIn("r01", text)
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir = Path(tmp) / "audio"
            audio_dir.mkdir()
            (audio_dir / "o01.wav").write_bytes(b"x")
            out = Path(tmp) / "manifest.jsonl"
            code, text = self.run_cli("init-manifest", "--audio-dir", str(audio_dir), "--out", str(out), "--speaker", "doctor_a")
            self.assertEqual(code, 0)
            lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([entry["id"] for entry in lines], ["o01"])
            self.assertEqual(lines[0]["speaker"], "doctor_a")
            self.assertIn("have no recording yet", text)


class Prompts(unittest.TestCase):
    prompts = [json.loads(line) for line in (ROOT / "evaluation" / "prompts" / "prompts.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.startswith("#")]

    def test_ids_are_unique_and_cover_the_three_specialties(self):
        ids = [p["id"] for p in self.prompts]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({p["specialty"] for p in self.prompts}, {"respiratory_critical_care", "orthopedics", "infectious_disease"})

    def test_prompts_contain_no_personal_information(self):
        for prompt in self.prompts:
            for text in (prompt["spoken"], prompt["reference"]):
                self.assertEqual(dataset.privacy_findings(text), [], prompt["id"])

    def test_each_reference_is_what_the_pipeline_produces(self):
        """Keeps the prompts and the normalisation rules in step. Time and counting words
        (三年, 五天) are not converted by design (D6), so numerals are compared canonically."""
        from server import app as srv
        for prompt in self.prompts:
            produced = srv.postprocess_transcript(prompt["spoken"], "zh-CN", [prompt["specialty"]])[0]
            self.assertEqual(metrics.cer(prompt["reference"], produced, canonical=True).edits, 0,
                             f"{prompt['id']}: {produced}")
            for quantity in metrics.extract_quantities(prompt["reference"]):
                self.assertIn(quantity, produced, f"{prompt['id']}: {quantity}")


if __name__ == "__main__":
    unittest.main()
