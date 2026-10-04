"""Upload sessions and the scan itself.

The doctor picks case files in the web page; the page sends them to this local service in batches. The
files are held **in memory only** (never written to disk, never logged) until the doctor closes the dialog,
the scan result has been used, or `SESSION_TTL` seconds pass without activity.
"""
import secrets
import threading
import time
from dataclasses import dataclass, field

from . import mining, privacy, readers

SESSION_TTL = 30 * 60
MAX_SESSION_BYTES = 800 * 1024 * 1024
MAX_SESSION_FILES = 100_000
MIN_CASES_FLOOR = 3          # below this a word could single out a patient with several records


class LimitError(Exception):
    pass


@dataclass
class Session:
    id: str
    texts: list = field(default_factory=list)       # decoded text of each accepted file
    skipped: list = field(default_factory=list)     # (file name, reason): names only
    total_bytes: int = 0
    touched: float = field(default_factory=time.time)
    scan: dict | None = None                        # last result: only words and counts
    busy: threading.Lock = field(default_factory=threading.Lock)


class Store:
    """At most one session at a time: a new one wipes the previous one."""

    def __init__(self):
        self._lock = threading.Lock()
        self._session: Session | None = None

    def create(self) -> Session:
        with self._lock:
            self._session = Session(id=secrets.token_urlsafe(12))
            return self._session

    def get(self, session_id: str | None) -> Session | None:
        with self._lock:
            session = self._session
            if session is None or session.id != session_id:
                return None
            if time.time() - session.touched > SESSION_TTL:
                self._session = None
                return None
            session.touched = time.time()
            return session

    def clear(self, session_id: str | None = None) -> bool:
        with self._lock:
            if self._session is not None and (session_id is None or self._session.id == session_id):
                self._session = None
                return True
            return False

    def active(self) -> bool:
        with self._lock:
            return self._session is not None and time.time() - self._session.touched <= SESSION_TTL


store = Store()


def add_files(session: Session, items: list[tuple[str, bytes]]) -> dict:
    """Add one batch. Unreadable files are listed (by name) and skipped; limits raise LimitError."""
    accepted = 0
    for name, raw in items:
        if session.total_bytes + len(raw) > MAX_SESSION_BYTES or len(session.texts) >= MAX_SESSION_FILES:
            raise LimitError("病例总量过大，请分批处理（每次不超过 800 MB 或 10 万个文件）")
        try:
            text = readers.read_upload(name, raw)
        except ValueError as exc:
            session.skipped.append((name, str(exc)))
            continue
        session.texts.append(text)
        session.total_bytes += len(raw)
        accepted += 1
    session.scan = None                              # a new batch invalidates an earlier result
    return {"accepted": accepted, "files": len(session.texts), "bytes": session.total_bytes,
            "skipped": [{"name": n, "reason": r} for n, r in session.skipped[-50:]], "skipped_count": len(session.skipped)}


def scan(session: Session, settings: mining.Settings, known: set[str], case_start: str | None = None) -> dict:
    """Mine the session's cases. Returns words and counts only; the session keeps the texts for a re-run."""
    if settings.min_cases < MIN_CASES_FLOOR:
        raise ValueError(f"门槛不能低于 {MIN_CASES_FLOOR} 份病例（太低可能让个别患者的特殊用词出现在词表里）")
    try:
        import jieba
        import jieba.posseg as posseg
    except ImportError as exc:
        raise RuntimeError("缺少 jieba，无法识别人名、地名，已停止") from exc
    jieba.setLogLevel(60)
    jieba.initialize()                               # jieba.dt.FREQ is only the real table after this

    raw_cases = [case for text in session.texts for case in readers.split_cases(text, case_start)]
    if len(raw_cases) < settings.min_cases:
        raise ValueError(f"病例只有 {len(raw_cases)} 份，少于门槛 {settings.min_cases}，不可能得到满足门槛的词")
    names: set[str] = set()
    for text in raw_cases:
        names |= privacy.harvest_names(text)
    cases = [mining.prepare_case(text, names) for text in raw_cases]
    del raw_cases
    result = mining.mine(cases, settings, known, names, tagger=posseg.cut, jieba_freq=jieba.dt.FREQ)
    output = {
        "cases": len(cases),
        "files": len(session.texts),
        "names_removed": len(names),                  # a number, never the names
        "settings": {"min_cases": settings.min_cases, "min_pmi": settings.min_pmi, "min_entropy": settings.min_entropy,
                     "common_freq": settings.common_freq},
        "stats": result.stats,
        "candidates": result.candidates,
        "skipped": [{"name": n, "reason": r} for n, r in session.skipped[:50]],
        "skipped_count": len(session.skipped),
    }
    session.scan = output
    return output
