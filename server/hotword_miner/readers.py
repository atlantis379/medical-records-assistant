"""Read case files (.txt, .docx) that the doctor picked in the web page. Everything stays in memory.

.docx is read with the standard library only (a .docx is a zip file with word/document.xml), so no extra
package is needed on a hospital computer. The old binary .doc format is reported, not guessed at.
"""
import io
import re
import zipfile
from xml.etree import ElementTree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_DOCX_XML_BYTES = 200 * 1024 * 1024        # guards against a zip bomb
TEXT_ENCODINGS = ("utf-8-sig", "gb18030")       # Windows exports are often GBK
SUPPORTED = (".txt", ".docx")


def read_txt(raw: bytes) -> str:
    for encoding in TEXT_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别文本编码（支持 UTF-8 和 GBK）")


def read_docx(raw: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise ValueError("文档内容过大")
            root = ElementTree.fromstring(archive.read(info))
    except zipfile.BadZipFile as exc:
        raise ValueError("不是有效的 .docx 文件（可能是加密的或已损坏）") from exc
    except KeyError as exc:
        raise ValueError("缺少文档正文") from exc
    except ElementTree.ParseError as exc:
        raise ValueError("文档内容无法解析") from exc
    lines = []
    for paragraph in root.iter(W + "p"):          # body and table cells, in document order
        parts = []
        for node in paragraph.iter():
            if node.tag == W + "t" and node.text:
                parts.append(node.text)
            elif node.tag in (W + "tab", W + "br"):
                parts.append(" ")
        lines.append("".join(parts))
    return "\n".join(lines)


def read_upload(name: str, raw: bytes) -> str:
    """Text of one uploaded file. Raises ValueError with a reason the doctor can act on."""
    lower = name.lower()
    if lower.startswith(("~$", ".")):
        raise ValueError("临时文件")
    if lower.endswith(".doc"):
        raise ValueError("旧版 .doc 格式不支持，请在 Word 中另存为 .docx")
    if lower.endswith(".docx"):
        return read_docx(raw)
    if lower.endswith(".txt"):
        return read_txt(raw)
    raise ValueError("不是 .txt 或 .docx 文件")


def split_cases(text: str, case_start: str | None) -> list[str]:
    """One file is one case, unless `case_start` (plain text, not a pattern) marks where each case begins."""
    case_start = (case_start or "").strip()
    if not case_start:
        return [text]
    starts = [m.start() for m in re.finditer(r"^[ \t]*" + re.escape(case_start), text, re.MULTILINE)]
    if not starts:
        return [text]
    bounds = ([0] if starts[0] != 0 else []) + starts + [len(text)]
    return [text[a:b] for a, b in zip(bounds, bounds[1:]) if text[a:b].strip()]
