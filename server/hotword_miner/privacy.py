"""De-identification for the mining step.

The tool never outputs sentences, only words that occur in many different cases, so most identifying
detail cannot appear in the result anyway. This module adds layers on top of that:

* digits, dates, ID numbers, phone numbers and e-mail addresses split the text, so they never become words;
* names that follow a label ("姓名：", "患者", "医生" ...) are collected from the whole corpus, removed from
  the text and banned from the result;
* a candidate that looks like a person, place or organisation name is dropped.

None of this is perfect (see README): the doctor's review of the candidate list is the final check.
"""
import re

SURNAMES = ("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾萧田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤"
            "欧司诸上东皇尉公慕令")

# "姓名：张三" needs no surname check; the other labels do, so "患者因咳嗽" is not taken for a name
NAME_LABEL = re.compile(r"姓\s*名\s*[:：]?\s*([一-鿿·]{2,4})")
ROLE_LABEL = re.compile(
    r"(?:患者|病人|患儿|病患|家属|联系人|监护人|送检医生|申请医生|主治医师|主治医生|主任医师|副主任医师|住院医师|医师|医生|签名|报告人|审核者|审核|录入|操作者|检查者|护士)"
    r"\s*[:：]?\s*([" + SURNAMES + r"][一-鿿·]{1,2})")

PRIVATE_PATTERNS = [
    re.compile(r"[0-9Xx]{15,18}"),                       # ID numbers
    re.compile(r"1[3-9]\d{9}"),                          # mobile phones
    re.compile(r"\d{3,4}[-－]\d{7,8}"),                  # landlines
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),             # e-mail
    re.compile(r"https?://\S+"),
]
DIGITS = re.compile(r"[0-9０-９]+")
CJK_RUN = re.compile(r"[一-鿿]+")
LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\-+]*")


PLACE_SUFFIXES = ("医院", "卫生院", "诊所", "中心", "大学", "学院", "公司", "研究所", "县", "市", "省", "区", "镇", "乡", "村", "街道", "路", "小区", "社区")


def harvest_names(text: str) -> set[str]:
    """Names (and their 2- and 3-character prefixes, because the match may run into the next word)."""
    found: set[str] = set()
    for pattern in (NAME_LABEL, ROLE_LABEL):
        for match in pattern.finditer(text):
            name = match.group(1).replace("·", "")
            for size in (2, 3, len(name)):
                if 2 <= size <= len(name):
                    found.add(name[:size])
    return found


def mask(text: str, names: set[str]) -> str:
    """Replace identifying strings with a separator so that no word can be built across them."""
    for pattern in PRIVATE_PATTERNS:
        text = pattern.sub("，", text)
    for name in sorted(names, key=len, reverse=True):
        text = text.replace(name, "，")
    return text


def acceptable_latin(token: str) -> bool:
    """Abbreviations such as CT, COPD, PaO2, CA125 are fine; case or record numbers (A123456) are not."""
    letters = sum(c.isalpha() for c in token)
    digits = sum(c.isdigit() for c in token)
    return 2 <= len(token) <= 14 and letters >= 2 and digits <= 3


def looks_like_name(word: str, tagger) -> str | None:
    """Reason string if `word` looks like a person, place or organisation name, otherwise None.

    `tagger` is jieba.posseg.cut. Only the whole word counts: 马凡综合征 contains a name-like token
    but is a medical term, 兰坪县人民医院 contains a place and is not.
    """
    pairs = [(p.word, p.flag) for p in tagger(word)]
    # drug names such as 奥马珠单抗 contain a token tagged as a place, so a token alone is not enough
    if any(flag.startswith(("ns", "nt")) for _, flag in pairs) and (len(pairs) == 1 or word.endswith(PLACE_SUFFIXES)):
        return "疑似地名或机构名"
    first, first_flag = pairs[0]
    if first_flag.startswith("nr") and len(word) - len(first) <= 1:
        return "疑似人名"
    return None
