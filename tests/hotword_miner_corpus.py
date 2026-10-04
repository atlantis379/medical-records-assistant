"""A synthetic corpus for the hotword miner tests. No real case is used anywhere.

Planted facts the tests rely on:
* TERMS occur in many cases (they must be found);
* RARE occurs in only a few cases (below the threshold, must not appear);
* patient names, phone numbers, ID numbers and record numbers occur around them (must never appear).
"""
import io
import random
import zipfile

TERMS = ["肺间质纤维化", "纵隔淋巴结肿大", "胸腔积液", "肺栓塞", "支气管扩张", "奥马珠单抗", "无创呼吸机辅助通气", "磨玻璃影", "肺动脉高压", "呼吸衰竭"]
RARE = "罕见酪氨酸激酶抑制剂"          # only in a handful of cases
BELOW = "肺泡微石症"                    # in 4 cases: just under a threshold of 5
NAMES = ["张建国", "李秀英", "王明华", "赵志强", "刘芳", "陈晓东", "杨丽娟", "黄伟"]
HOSPITAL_PLACE = "兰坪县人民医院"

TEMPLATES = [
    "胸部CT提示{t}，建议3个月后随访。",
    "考虑{t}，予对症治疗并密切观察。",
    "既往有{t}病史，规律服药。",
    "查体：双肺呼吸音粗，{t}待排。",
    "入院后完善检查，结果示{t}，请上级医师会诊。",
    "诊断：{t}，收入呼吸内科。",
    "患者自述{t}多年，近期加重。",
    "复查提示{t}较前好转。",
]
FILLER = ["患者今日精神可，饮食睡眠一般。", "无发热，无咯血，二便正常。", "继续当前治疗方案，门诊复诊。", "家属已知情并同意。"]


def make_case(rng: random.Random, index: int, *, rare: bool = False, below: bool = False) -> str:
    name = NAMES[index % len(NAMES)]
    lines = [f"姓名：{name}  住院号：{rng.randint(10**8, 10**9 - 1)}  电话：1{rng.randint(3, 9)}{rng.randint(10**8, 10**9 - 1)}",
             f"身份证：{rng.randint(10**5, 10**6 - 1)}19{rng.randint(50, 99)}0{rng.randint(1, 9)}1{rng.randint(1, 9)}{rng.randint(1000, 9999)}",
             f"就诊医院：{HOSPITAL_PLACE}",
             f"主诉：咳嗽咳痰{rng.randint(1, 30)}天，加重{rng.randint(1, 5)}天。"]
    lines.append("现病史：" + FILLER[rng.randrange(len(FILLER))] + f"患者{name}因咳嗽就诊。")
    section = ["辅助检查：", "诊断：", "处理："]
    for head in section:
        picks = rng.sample(TERMS, 3)
        text = "".join(TEMPLATES[rng.randrange(len(TEMPLATES))].format(t=t) for t in picks)
        lines.append(head + text)
    if rare:
        lines.append("辅助检查：" + TEMPLATES[0].format(t=RARE))
    if below:
        lines.append("辅助检查：" + TEMPLATES[index % 4].format(t=BELOW))   # four different contexts, so boundary entropy is fine
    lines.append(f"主治医师：{NAMES[(index + 3) % len(NAMES)]}")
    return "\n".join(lines)


def make_corpus(count: int = 80, seed: int = 7) -> list[str]:
    rng = random.Random(seed)
    cases = []
    for i in range(count):
        cases.append(make_case(rng, i, rare=i < 2, below=i < 4))
    return cases


def docx_bytes(text: str) -> bytes:
    """A minimal valid .docx: one paragraph per line, the last line in a table cell."""
    def paragraph(line):
        escaped = line.replace("&", "&amp;").replace("<", "&lt;")
        return f"<w:p><w:r><w:t xml:space=\"preserve\">{escaped}</w:t></w:r></w:p>"

    lines = text.split("\n")
    body = "".join(paragraph(l) for l in lines[:-1]) + f"<w:tbl><w:tr><w:tc>{paragraph(lines[-1])}</w:tc></w:tr></w:tbl>"
    xml = ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
           "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body>" + body + "</w:body></w:document>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml)
        archive.writestr("[Content_Types].xml", "<Types/>")
    return buffer.getvalue()


def case_files(cases: list[str], docx_every: int = 2) -> list[tuple[str, bytes]]:
    """(file name, bytes) as the browser would send them: a mix of .docx, UTF-8 .txt and GBK .txt."""
    files = []
    for i, case in enumerate(cases):
        if i % docx_every == 0:
            files.append((f"case_{i:03d}.docx", docx_bytes(case)))
        else:
            files.append((f"case_{i:03d}.txt", case.encode("utf-8" if i % 4 else "gb18030")))
    return files
