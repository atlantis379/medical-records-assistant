"""Number + unit normalisation for dictated clinical text.

Rules live in docs/CLINICAL_NUMBER_UNIT_RULES.md; units and measure names live in
server/data/normalization/*.json so they can be reviewed and extended without code.

Principles (project decisions D1-D3):
* a number is converted only when it is followed by a known unit, or follows a known
  measure name; everything else is left exactly as spoken;
* when a reading is ambiguous or cannot be established, the text is left unchanged and
  a notice is returned so the interface can ask the clinician to check;
* units listed as `attach` hug the number (130mmHg, 37.5℃, 90次/分); all others are
  separated by one space (30 mg, 3.5 mmol/L).
"""
import json
import re
from pathlib import Path

from .numbers import CN_INTEGER_CHARS, CN_NUMBER_PATTERN, looks_approximate, parse_cn_number

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "normalization"
_MMHG_HOLD = "mmHg"   # keeps a failed blood-pressure span away from later passes

_NUM = rf"(?:(?<![A-Za-z\d.])\d+(?:\.\d+)?|(?<![几多]){CN_NUMBER_PATTERN}(?!(?<=千)[克帕]))"
_RANGE_SEP = r"(?:到|至|~|～|-)"
_BP_CHARS = rf"[0-9{CN_INTEGER_CHARS}到至~～\-/／，,、比.．]"
_BP_SEPS = "到至~～-/／，,、比.． "
_RANGE_CHARS = set("到至~～-")
_BOUNDARY_CHARS = set("/／，,、比")


class Registry:
    def __init__(self, data_dir: Path):
        units = json.loads((data_dir / "units.json").read_text(encoding="utf-8-sig"))
        measures = json.loads((data_dir / "measures.json").read_text(encoding="utf-8-sig"))

        self.unit_lookup: dict[str, tuple[str, str]] = {}
        alternatives: list[tuple[str, str]] = []
        for unit in units["units"]:
            guard = unit.get("not_followed_by")
            for spoken in unit["spoken"]:
                self.unit_lookup[spoken.casefold()] = (unit["symbol"], unit["style"])
                escaped = re.escape(spoken)
                alternatives.append((spoken, f"{escaped}(?!{guard})" if guard else escaped))
        alternatives.sort(key=lambda item: len(item[0]), reverse=True)
        self.unit_pattern = "|".join(alt for _, alt in alternatives)
        self.number_unit = re.compile(
            rf"(?P<n1>{_NUM})(?:\s*(?P<sep>{_RANGE_SEP})\s*(?P<n2>{_NUM}))?\s*(?P<unit>{self.unit_pattern})",
            re.IGNORECASE,
        )

        self.rate_prefixes = []
        for item in units.get("rate_prefixes", []):
            self.rate_prefixes.append((
                re.compile(rf"{re.escape(item['prefix'])}(?P<n>{_NUM}){re.escape(item['unit_spoken'])}"),
                item["symbol"],
            ))
        self.number_mult_unit = re.compile(
            rf"(?P<dims>{_NUM}(?:\s*(?:乘以|乘|×|x|\*)\s*{_NUM}){{1,2}})\s*(?P<unit>厘米|毫米|cm|mm)(?!汞)",
            re.IGNORECASE,
        )

        freq = units["frequencies"]
        self.per_day = re.compile(
            rf"(?P<words>{'|'.join(re.escape(w) for w in freq['per_day_words'])})(?P<n>{_NUM})次"
        )
        self.per_day_abbr = freq["per_day_abbr"]
        self.every_hours_template = freq["every_hours_abbr"]

        bp = measures["bp"]
        self.bp_systolic = tuple(bp["systolic"])
        self.bp_diastolic = tuple(bp["diastolic"])
        bp_names = "|".join(re.escape(n) for n in sorted(bp["names"], key=len, reverse=True))
        self.bp_with_unit = re.compile(
            rf"(?P<span>{_BP_CHARS}+?)\s*(?P<unit>毫米汞柱|mm\s*hg)", re.IGNORECASE
        )
        self.bp_named = re.compile(
            rf"(?<![高低])(?P<name>{bp_names})(?P<conn>[：:为是约]?)(?P<gap>\s*)(?P<span>{_BP_CHARS}+)(?!{_BP_CHARS})(?!\s*(?:毫米汞柱|mm\s*hg))",
            re.IGNORECASE,
        )

        names = sorted(measures["names"], key=lambda item: len(item["name"]), reverse=True)
        self.name_gap = {item["name"]: item.get("gap", "") for item in names}
        name_alt = "|".join(re.escape(item["name"]) for item in names)
        self.name_number = re.compile(
            rf"(?P<name>{name_alt})(?P<conn>为|是|约|达)?(?P<num>{CN_NUMBER_PATTERN})(?![{CN_INTEGER_CHARS}点几多米])(?!(?!{name_alt})[一-鿿])"
        )
        gap_names = "|".join(re.escape(item["name"]) for item in names if item.get("gap"))
        self.gap_re = re.compile(rf"(?P<name>{gap_names})(?=-?\d)") if gap_names else None

        angles = "|".join(re.escape(n) for n in sorted(measures["angle_names"], key=len, reverse=True))
        self.angle = re.compile(rf"(?P<name>{angles})(?P<n1>{_NUM})(?:(?P<sep>{_RANGE_SEP})(?P<n2>{_NUM}))?(?:度|°)")
        self.rate_names = "心率|脉搏|脉率|呼吸频率|呼吸"
        self.per_minute = re.compile(rf"(?P<name>{self.rate_names})每分钟?(?P<n>{_NUM})次")


_registry: Registry | None = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry(DATA_DIR)
    return _registry


def reload_registry(data_dir: Path | None = None) -> Registry:
    global _registry
    _registry = Registry(data_dir or DATA_DIR)
    return _registry


def _notice(code: str, source: str, message: str) -> dict:
    return {"code": code, "source": source, "message": message}


# ---------------------------------------------------------------- blood pressure
def _split_options(piece: str) -> list[tuple[str, str]]:
    """Ways to cut one run of digits into two numbers (only used when it is not one number)."""
    options = []
    if re.fullmatch(rf"[{CN_INTEGER_CHARS}]+", piece):
        for i in range(1, len(piece)):
            left, right = parse_cn_number(piece[:i]), parse_cn_number(piece[i:])
            if left is not None and right is not None and "." not in left + right:
                options.append((left, right))
    elif re.fullmatch(r"\d{5,6}", piece):
        for i in (2, 3):
            left, right = piece[:i], piece[i:]
            if left[0] != "0" and right[0] != "0" and len(right) >= 2:
                options.append((left, right))
    return options


def _bp_in_range(value: float, bounds: tuple) -> bool:
    return bounds[0] <= value <= bounds[1]


def _bp_candidate(numbers: list[str], seps: list[str], reg: Registry) -> str | None:
    """Return 'sys/dia' text if this reading is a plausible blood pressure, else None."""
    kinds = ["range" if s in _RANGE_CHARS else "boundary" for s in seps]
    if kinds == ["boundary"]:
        sys_vals, dia_vals = [numbers[0]], [numbers[1]]
    elif kinds == ["range", "boundary"]:
        sys_vals, dia_vals = numbers[0:2], [numbers[2]]
    elif kinds == ["boundary", "range"]:
        sys_vals, dia_vals = [numbers[0]], numbers[1:3]
    elif kinds == ["range", "boundary", "range"]:
        sys_vals, dia_vals = numbers[0:2], numbers[2:4]
    else:
        return None
    try:
        sys_f = [float(v) for v in sys_vals]
        dia_f = [float(v) for v in dia_vals]
    except ValueError:
        return None
    if not all(_bp_in_range(v, reg.bp_systolic) for v in sys_f):
        return None
    if not all(_bp_in_range(v, reg.bp_diastolic) for v in dia_f):
        return None
    if sys_f != sorted(sys_f) or dia_f != sorted(dia_f) or len(set(sys_f)) != len(sys_f) or len(set(dia_f)) != len(dia_f):
        return None
    if sys_f[0] <= dia_f[0] or sys_f[-1] <= dia_f[-1]:
        return None
    return "-".join(sys_vals) + "/" + "-".join(dia_vals)


def solve_blood_pressure(span: str, reg: Registry) -> tuple[str, str | None]:
    """('ok', '130/80') | ('single', '130' or '130-140') | ('ambiguous', None) | ('fail', None)."""
    pieces = re.split(r"([到至~～\-/／，,、比])", span)
    values, seps = pieces[0::2], pieces[1::2]
    if not values or any(v == "" for v in values):
        return "fail", None
    parsed = [parse_cn_number(v) for v in values]
    need_split = [i for i, p in enumerate(parsed) if p is None]
    candidates: set[str] = set()

    def consider(numbers: list[str], separators: list[str]):
        result = _bp_candidate(numbers, separators, reg)
        if result:
            candidates.add(result)

    if not need_split:
        if len(values) == 1 and not re.fullmatch(r"\d{5,6}", values[0]):
            return "single", parsed[0]
        if len(values) == 2 and seps[0] in _RANGE_CHARS and "." not in parsed[0] + parsed[1]:
            return "single", f"{parsed[0]}-{parsed[1]}"
        consider(parsed, seps)
        # a long digit run may still hide two numbers, e.g. 13080
        for i, value in enumerate(values):
            for left, right in _split_options(value) if re.fullmatch(r"\d{5,6}", value) else []:
                consider(parsed[:i] + [left, right] + parsed[i + 1:], seps[:i] + ["/"] + seps[i:])
    elif len(need_split) == 1:
        i = need_split[0]
        for left, right in _split_options(values[i]):
            consider(parsed[:i] + [left, right] + parsed[i + 1:], seps[:i] + ["/"] + seps[i:])
    if len(candidates) == 1:
        return "ok", next(iter(candidates))
    return ("ambiguous" if candidates else "fail"), None


def _trim_span(span: str) -> tuple[str, str, str]:
    stripped = span.strip(_BP_SEPS)
    if not stripped:
        return span, "", ""
    start = span.index(stripped)
    return span[:start], stripped, span[start + len(stripped):]


def _normalize_blood_pressure(text: str, reg: Registry, notes: list[dict]) -> str:
    def with_unit(match: re.Match) -> str:
        lead, core, trail = _trim_span(match.group("span"))
        if not core or not re.search(rf"[\d{CN_INTEGER_CHARS}]", core):
            return match.group(0)
        status, value = solve_blood_pressure(core, reg)
        if status == "ok":
            return f"{lead}{value}mmHg{trail}"
        if status == "single":
            return match.group(0)  # one value: the generic number+unit pass handles it
        notes.append(_notice("bp_unresolved", core, f"血压数字无法确定拆分，未转换，请核对：{core}"))
        return f"{match.group('span')}{_MMHG_HOLD}"

    text = reg.bp_with_unit.sub(with_unit, text)

    def named(match: re.Match) -> str:
        lead, core, trail = _trim_span(match.group("span"))
        if not core or not re.search(rf"[\d{CN_INTEGER_CHARS}]", core):
            return match.group(0)
        status, value = solve_blood_pressure(core, reg)
        head = f"{match.group('name')}{match.group('conn')}{match.group('gap')}{lead}"
        if status == "ok":
            return f"{head}{value}mmHg{trail}"
        if status == "single":
            following = trail[:1] or match.string[match.end():match.end() + 1]
            low = float(value.split("-")[0])
            if "一" <= following <= "鿿" or not _bp_in_range(low, reg.bp_systolic):
                return match.group(0)   # 血压五年: not a reading
            return f"{head}{value}mmHg{trail}"
        notes.append(_notice("bp_unresolved", core, f"血压数字无法确定拆分，未转换，请核对：{core}"))
        return match.group(0)

    return reg.bp_named.sub(named, text)


# ---------------------------------------------------------------- generic passes
def _format(number: str, symbol: str, style: str) -> str:
    return f"{number}{symbol}" if style == "attach" else f"{number} {symbol}"


def _lead_space(text: str, start: int, style: str) -> str:
    """A spaced-style quantity that directly follows Chinese text gets a space first (美罗培南 1 g)."""
    if style != "space" or start == 0:
        return ""
    return " " if "一" <= text[start - 1] <= "鿿" else ""


def _unparsed(token: str, notes: list[dict], label: str = "数字", anchored: bool = False) -> None:
    """Report a number we declined to convert. An approximation (三四十) is silent unless a
    measure name or unit makes it a value (血氧九五)."""
    if anchored or not looks_approximate(token):
        notes.append(_notice("number_unparsed", token, f"{label}“{token}”无法确定读法，未转换，请核对。"))


def _normalize_percent(text: str, notes: list[dict]) -> str:
    pattern = re.compile(rf"百分之(?P<n1>{_NUM})(?:{_RANGE_SEP}(?P<n2>{_NUM}))?")

    def repl(match: re.Match) -> str:
        first = parse_cn_number(match.group("n1"))
        second = parse_cn_number(match.group("n2")) if match.group("n2") else None
        if first is None or (match.group("n2") and second is None):
            _unparsed(match.group("n1"), notes)
            return match.group(0)
        return f"{first}-{second}%" if second is not None else f"{first}%"

    return pattern.sub(repl, text)


def _normalize_units(text: str, reg: Registry, notes: list[dict]) -> str:
    def repl(match: re.Match) -> str:
        first = parse_cn_number(match.group("n1"))
        second = parse_cn_number(match.group("n2")) if match.group("n2") else None
        if first is None or (match.group("n2") and second is None):
            _unparsed(match.group("n1") if first is None else match.group("n2"), notes)
            return match.group(0)
        symbol, style = reg.unit_lookup[match.group("unit").casefold()]
        number = f"{first}-{second}" if second is not None else first
        return _lead_space(match.string, match.start(), style) + _format(number, symbol, style)

    return reg.number_unit.sub(repl, text)


def _normalize_rate_prefixes(text: str, reg: Registry, notes: list[dict]) -> str:
    """每分钟三升 -> 3 L/min, 每小时五十毫升 -> 50 mL/h."""
    for pattern, symbol in reg.rate_prefixes:
        def repl(match: re.Match, symbol=symbol) -> str:
            number = parse_cn_number(match.group("n"))
            if number is None:
                _unparsed(match.group("n"), notes)
                return match.group(0)
            return _lead_space(match.string, match.start(), "space") + f"{number} {symbol}"

        text = pattern.sub(repl, text)
    return text


def _normalize_sizes(text: str, reg: Registry, notes: list[dict]) -> str:
    """三乘二厘米 -> 3×2 cm."""
    def repl(match: re.Match) -> str:
        parts = re.split(r"\s*(?:乘以|乘|×|x|\*)\s*", match.group("dims"), flags=re.IGNORECASE)
        numbers = [parse_cn_number(part) for part in parts]
        if any(n is None for n in numbers):
            for part, number in zip(parts, numbers):
                if number is None:
                    _unparsed(part, notes)
            return match.group(0)
        symbol, style = reg.unit_lookup[match.group("unit").casefold()]
        return _lead_space(match.string, match.start(), style) + _format("×".join(numbers), symbol, style)

    return reg.number_mult_unit.sub(repl, text)


def _normalize_rates(text: str, reg: Registry, notes: list[dict]) -> str:
    def repl(match: re.Match) -> str:
        number = parse_cn_number(match.group("n"))
        if number is None:
            _unparsed(match.group("n"), notes)
            return match.group(0)
        return f"{match.group('name')}{number}次/分"

    return reg.per_minute.sub(repl, text)


def _normalize_name_numbers(text: str, reg: Registry, notes: list[dict]) -> str:
    def repl(match: re.Match) -> str:
        number = parse_cn_number(match.group("num"))
        if number is None:
            _unparsed(match.group("num"), notes, f"{match.group('name')}的数字", anchored=True)
            return match.group(0)
        return f"{match.group('name')}{match.group('conn') or ''}{number}"

    return reg.name_number.sub(repl, text)


def _normalize_angles(text: str, reg: Registry, notes: list[dict]) -> str:
    def repl(match: re.Match) -> str:
        first = parse_cn_number(match.group("n1"))
        second = parse_cn_number(match.group("n2")) if match.group("n2") else None
        if first is None or (match.group("n2") and second is None):
            _unparsed(match.group("n1"), notes, f"{match.group('name')}的角度")
            return match.group(0)
        number = f"{first}-{second}" if second is not None else first
        return f"{match.group('name')}{number}°"

    return reg.angle.sub(repl, text)


def _space_before_abbreviation(text: str, start: int) -> str:
    return " " if start > 0 and (text[start - 1].isascii() and not text[start - 1].isspace() or text[start - 1] in "%℃") else ""


def _normalize_frequencies(text: str, reg: Registry) -> str:
    def per_day(match: re.Match) -> str:
        number = parse_cn_number(match.group("n"))
        abbr = reg.per_day_abbr.get(number or "")
        if not abbr:
            return match.group(0)
        return _space_before_abbreviation(match.string, match.start()) + abbr

    text = reg.per_day.sub(per_day, text)

    def every_hours(match: re.Match) -> str:
        number = parse_cn_number(match.group("n"))
        if number is None or not number.isdigit() or not 1 <= int(number) <= 48:
            return match.group(0)
        return _space_before_abbreviation(match.string, match.start()) + reg.every_hours_template.format(n=int(number))

    return re.compile(rf"每(?P<n>{_NUM})小时一次").sub(every_hours, text)


def _apply_name_gaps(text: str, reg: Registry) -> str:
    if reg.gap_re is None:
        return text
    return reg.gap_re.sub(lambda m: f"{m.group('name')}{reg.name_gap[m.group('name')]}", text)


def normalize_quantities(text: str, registry: Registry | None = None) -> tuple[str, list[dict]]:
    reg = registry or get_registry()
    notes: list[dict] = []
    value = text or ""
    value = _normalize_blood_pressure(value, reg, notes)
    value = _normalize_percent(value, notes)
    value = _normalize_rates(value, reg, notes)
    value = _normalize_rate_prefixes(value, reg, notes)
    value = _normalize_sizes(value, reg, notes)
    value = _normalize_units(value, reg, notes)
    value = _normalize_angles(value, reg, notes)
    value = _normalize_name_numbers(value, reg, notes)
    value = _normalize_frequencies(value, reg)
    value = _apply_name_gaps(value, reg)
    value = value.replace(_MMHG_HOLD, "mmHg")
    return value, notes
