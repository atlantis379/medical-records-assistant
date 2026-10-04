"""Spoken Chinese numerals -> Arabic digits.

Deliberately conservative (docs/CLINICAL_NUMBER_UNIT_RULES.md, section 4.3): a token
is converted only when it has exactly one sensible reading. Approximate numbers
such as 三四 or 一两 stay Chinese, as GB/T 15835 requires, and so does anything
malformed. `parse_cn_number` returns None whenever it declines.
"""
import re

DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}
CN_DIGIT_CHARS = "零〇一二两三四五六七八九"
CN_INTEGER_CHARS = CN_DIGIT_CHARS + "十百千万"
# One spoken number: optional 负, integer part, optional 点 + decimal digits.
CN_NUMBER_PATTERN = rf"负?[{CN_INTEGER_CHARS}]+(?:点[{CN_DIGIT_CHARS}]+)?"
ARABIC_NUMBER_PATTERN = r"-?\d+(?:\.\d+)?"
NUMBER_PATTERN = rf"(?:{ARABIC_NUMBER_PATTERN}|{CN_NUMBER_PATTERN})"

_ADJACENT_DIGITS = re.compile(rf"[{CN_DIGIT_CHARS.replace('零', '').replace('〇', '')}]{{2,}}")


def parse_cn_integer(text: str) -> int | None:
    if not text:
        return None
    if all(ch in DIGITS for ch in text):
        if len(text) == 1:
            return DIGITS[text]
        # 一零二 -> 102 is a digit-by-digit reading and needs a zero in it.
        if text[0] not in "零〇" and any(ch in "零〇" for ch in text):
            return int("".join(str(DIGITS[ch]) for ch in text))
        return None  # 三四 / 一两 are approximations, not 34 / 12

    total = 0      # completed 万 groups
    section = 0    # value inside the current group
    digit: int | None = None
    zero_seen = False
    last_unit = 0
    prev_unit = 10 ** 6  # units must descend inside a group
    for ch in text:
        if ch in DIGITS:
            if ch in "零〇":
                if digit is not None:
                    return None
                zero_seen = True
            else:
                if digit is not None:
                    return None
                digit = DIGITS[ch]
        elif ch in SMALL_UNITS:
            unit = SMALL_UNITS[ch]
            if unit >= prev_unit:
                return None
            if digit is None:
                if ch == "十" and section == 0 and not zero_seen:
                    digit = 1  # 十五, 十
                else:
                    return None
            section += digit * unit
            digit = None
            zero_seen = False
            last_unit = unit
            prev_unit = unit
        elif ch == "万":
            group = section + (digit or 0)
            if group == 0:
                return None
            total += group * 10000
            section = 0
            digit = None
            zero_seen = False
            last_unit = 10000
            prev_unit = 10 ** 6
        else:
            return None
    if digit is not None:
        if zero_seen:
            section += digit              # 一百零二
        elif last_unit >= 100:
            section += digit * (last_unit // 10)   # 一百二 = 120, 一千二 = 1200, 一万五 = 15000
        else:
            section += digit
    return total + section


def parse_cn_number(token: str) -> str | None:
    """'三十八点五' -> '38.5', '负二点五' -> '-2.5', '120' -> '120'; None if not safely convertible."""
    value = (token or "").strip().replace("．", ".")
    if not value:
        return None
    if re.fullmatch(ARABIC_NUMBER_PATTERN, value):
        return value
    negative = value.startswith("负")
    if negative:
        value = value[1:]
    if not value:
        return None
    if "点" in value:
        left, right = value.split("点", 1)
        if not left or not right or any(ch not in DIGITS for ch in right):
            return None
        integer = parse_cn_integer(left)
        if integer is None:
            return None
        result = f"{integer}.{''.join(str(DIGITS[ch]) for ch in right)}"
    else:
        integer = parse_cn_integer(value)
        if integer is None:
            return None
        result = str(integer)
    return ("-" if negative else "") + result


def looks_approximate(token: str) -> bool:
    """三四, 一两, 几十: approximations that must stay as spoken (no notice needed)."""
    integer_part = token.split("点", 1)[0]
    return "几" in token or bool(_ADJACENT_DIGITS.search(integer_part))
