"""Recognizer back ends. Models are never downloaded implicitly: hospital machines are offline."""
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .dataset import Sample, audio_info


class BackendUnavailable(RuntimeError):
    """The back end cannot run here (missing model, missing package ...). The config is skipped."""


@dataclass
class RawResult:
    text: str
    audio_seconds: float
    infer_seconds: float
    hotword_count: int = 0


class Backend:
    name = "backend"

    def prepare(self) -> None:
        """Load models. Raise BackendUnavailable if that is impossible."""

    def warmup(self, sample: Sample, profile: dict) -> None:
        """Run once so model loading and CUDA start-up do not count towards RTF."""

    def transcribe(self, sample: Sample, hotword: str, profile: dict) -> RawResult:
        raise NotImplementedError


class TranscriptBackend(Backend):
    """Uses the sample's own `hypothesis`: evaluates the text pipeline without audio."""
    name = "transcripts"

    def transcribe(self, sample: Sample, hotword: str, profile: dict) -> RawResult:
        if sample.hypothesis is None:
            raise BackendUnavailable(f"sample {sample.id} has no 'hypothesis' text")
        seconds = audio_info(sample.audio)["seconds"] if sample.audio and sample.audio.exists() else 0.0
        return RawResult(sample.hypothesis, seconds, 0.0, len(hotword.split()) if hotword else 0)


class ParaformerBackend(Backend):
    """The production recognizer, loaded through server/app.py so settings match the service."""
    name = "paraformer"

    def __init__(self):
        self.srv = None
        self.model = None

    def prepare(self) -> None:
        try:
            from server import app as srv
            self.srv = srv
            self.model = srv.get_model()
        except Exception as exc:  # missing funasr / model files
            raise BackendUnavailable(f"Paraformer could not be loaded: {exc}") from exc

    def _generate(self, sample: Sample, hotword: str, profile: dict):
        kwargs = {"input": str(sample.audio), "batch_size_s": profile["batch_size_s"]}
        if hotword:
            kwargs["hotword"] = hotword
        started = time.perf_counter()
        result = self.srv.run_batch_generate(self.model, kwargs)
        return (result[0].get("text", "") if result else ""), time.perf_counter() - started

    def warmup(self, sample: Sample, profile: dict) -> None:
        if sample.audio and sample.audio.exists():
            self._generate(sample, "", profile)

    def transcribe(self, sample: Sample, hotword: str, profile: dict) -> RawResult:
        if not sample.audio or not sample.audio.exists():
            raise BackendUnavailable(f"sample {sample.id} has no audio file")
        text, elapsed = self._generate(sample, hotword, profile)
        return RawResult(text, audio_info(sample.audio)["seconds"], elapsed, len(hotword.split()) if hotword else 0)


class SenseVoiceBackend(Backend):
    """SenseVoice-Small through FunASR, if its model is already in the local cache.

    No hotword support; the output carries emotion/event tags which are stripped.
    """
    name = "sensevoice"

    def __init__(self):
        self.model = None

    @staticmethod
    def find_model() -> Path | None:
        roots = [Path(p) for p in os.getenv("MODELSCOPE_CACHE", "").split(os.pathsep) if p]
        roots += [Path.home() / ".cache" / "modelscope"]
        for root in roots:
            for base in (root, root / "hub"):
                for candidate in (base / "models" / "iic" / "SenseVoiceSmall", base / "iic" / "SenseVoiceSmall"):
                    if (candidate / "config.yaml").exists():
                        return candidate
        return None

    def prepare(self) -> None:
        path = self.find_model()
        if path is None:
            raise BackendUnavailable("SenseVoiceSmall is not in the local ModelScope cache (iic/SenseVoiceSmall); "
                                     "it is never downloaded automatically")
        try:
            from funasr import AutoModel
            self.model = AutoModel(model=str(path), device=os.getenv("ASR_DEVICE", "cpu"), disable_update=True)
        except Exception as exc:
            raise BackendUnavailable(f"SenseVoiceSmall could not be loaded: {exc}") from exc

    def _generate(self, sample: Sample) -> tuple[str, float]:
        from funasr.utils.postprocess_utils import rich_transcription_postprocess
        started = time.perf_counter()
        result = self.model.generate(input=str(sample.audio), language="zh", use_itn=True)
        elapsed = time.perf_counter() - started
        text = rich_transcription_postprocess(result[0]["text"]) if result else ""
        return text, elapsed

    def warmup(self, sample: Sample, profile: dict) -> None:
        if sample.audio and sample.audio.exists():
            self._generate(sample)

    def transcribe(self, sample: Sample, hotword: str, profile: dict) -> RawResult:
        if not sample.audio or not sample.audio.exists():
            raise BackendUnavailable(f"sample {sample.id} has no audio file")
        text, elapsed = self._generate(sample)
        return RawResult(text, audio_info(sample.audio)["seconds"], elapsed, 0)


BACKENDS = {"transcripts": TranscriptBackend, "paraformer": ParaformerBackend, "sensevoice": SenseVoiceBackend}
