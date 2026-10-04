"""The OCR engine (RapidOCR with the PP-OCRv4 mobile Chinese models, run by onnxruntime on the CPU)."""
import threading
import time

MAX_LONG_SIDE = 3200            # larger pictures are scaled down; phone photos are far bigger than the text needs
_engine = None
_lock = threading.Lock()


class OcrUnavailable(RuntimeError):
    pass


class BadImage(ValueError):
    pass


def unavailable_reason() -> str | None:
    """None when the engine can run, otherwise a sentence for the doctor."""
    try:
        import cv2  # noqa: F401
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError as exc:
        return f"这个安装包没有包含图片识别组件（{exc.name}）"
    return None


def decode(data: bytes):
    import cv2
    import numpy as np
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise BadImage("无法读取这张图片（格式不支持或文件已损坏；iPhone 的 HEIC 请先转成 JPG）")
    return image


def rotate(image, degrees: int):
    import cv2
    degrees %= 360
    codes = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    return cv2.rotate(image, codes[degrees]) if degrees in codes else image


def shrink(image):
    import cv2
    height, width = image.shape[:2]
    scale = MAX_LONG_SIDE / max(height, width)
    return cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else image


def image_size(data: bytes) -> tuple[int, int]:
    height, width = decode(data).shape[:2]
    return width, height


def recognize(data: bytes, degrees: int = 0) -> dict:
    """{"lines": [{"text", "score", "x", "y", "w", "h"}], "width", "height", "seconds"}, lines top to bottom."""
    reason = unavailable_reason()
    if reason:
        raise OcrUnavailable(reason)
    global _engine
    image = shrink(rotate(decode(data), degrees))
    height, width = image.shape[:2]
    with _lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
        started = time.perf_counter()
        found, _ = _engine(image)
        seconds = time.perf_counter() - started
    lines = []
    for box, text, score in found or []:
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        text = str(text).strip()
        if text:
            lines.append({"text": text, "score": float(score), "x": min(xs), "y": min(ys), "w": max(xs) - min(xs), "h": max(ys) - min(ys)})
    lines.sort(key=lambda line: (round(line["y"] / max(line["h"], 1)), line["x"]))
    return {"lines": lines, "width": width, "height": height, "seconds": seconds}
