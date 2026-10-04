"""Suggestions for characters the recogniser took for a look-alike (睾丸 read as 辜丸).

The recogniser's own confidence does not show these errors (they score 0.9 and more), so the written terms are compared
with a list of known terms: a text window that differs from a known term in exactly one character, where the two
characters are easily mistaken for each other, is *suggested*. Nothing is replaced; the doctor decides.
"""
import re

# characters that OCR (and handwriting) confuse; each string is one group
GROUPS = [
    "睾辜擎", "鞘朝稍梢销", "紊素索系", "颏额颔颠颊", "盂孟盅", "己已巳", "未末", "戊戌戍成", "大太犬", "日曰目", "土士", "间问闻",
    "苷甘", "氨安", "霉梅", "孢胞包", "唑座坐挫", "溴嗅", "胺铵", "嗪秦", "钙丐", "囊嚢", "炎淡", "粘黏", "斑班", "影彰", "结洁",
    "胆旦", "腔胜", "膈隔", "肋助", "椎锥推", "髋宽", "胫径", "腓非", "髌膑", "踝裸", "肱宏", "桡浇", "髂骼", "膜漠摸", "瘤溜",
    "灶火", "壁璧", "肌机饥", "腱键健", "腹复", "贲奔", "幽幼", "肠场", "尿屎", "膀榜", "疝仙", "阻组租", "塞赛寨", "扩广", "狭峡",
    "梗硬埂", "渗惨", "肿种钟", "增赠", "密蜜", "弥迷", "漫慢", "宫官", "卵孵", "附付", "脾牌", "胰姨", "淋林", "痰淡",
    "咳亥", "喘揣", "疱泡", "疹诊", "肛肝", "盆盘", "骶低", "跖跗", "腕脘", "掌堂", "趾址", "膝漆",
]
NEIGHBOURS: dict[str, set[str]] = {}
for _group in GROUPS:
    for _char in _group:
        NEIGHBOURS.setdefault(_char, set()).update(set(_group) - {_char})

CJK_RUN = re.compile(r"[一-鿿]{2,}")
CJK_WORD = re.compile(r"[一-鿿]{2,10}")
COMMON_FREQ = 300


class Index:
    def __init__(self, words):
        self.words = {w for w in words if CJK_WORD.fullmatch(w)}
        self.variants: dict[str, set[str]] = {}
        for word in self.words:
            for i, char in enumerate(word):
                for other in NEIGHBOURS.get(char, ()):
                    self.variants.setdefault(word[:i] + other + word[i + 1:], set()).add(word)
        self.lengths = sorted({len(w) for w in self.words}, reverse=True)


def _common(window: str) -> bool:
    try:
        import jieba
        jieba.setLogLevel(60)
        if not jieba.dt.initialized:
            jieba.initialize()
        return jieba.dt.FREQ.get(window, 0) >= COMMON_FREQ
    except ImportError:
        return False


def suggest(text: str, index: Index) -> list[dict]:
    found = []
    for run in CJK_RUN.finditer(text):
        part, base = run.group(), run.start()
        taken = [False] * len(part)
        for length in index.lengths:
            for i in range(len(part) - length + 1):
                if any(taken[i:i + length]):
                    continue
                window = part[i:i + length]
                if window in index.words:
                    taken[i:i + length] = [True] * length
                    continue
                options = index.variants.get(window)
                if options and not _common(window):
                    found.append({"from": window, "options": sorted(options)[:3], "start": base + i, "reason": "字形相近"})
                    taken[i:i + length] = [True] * length
    return found
