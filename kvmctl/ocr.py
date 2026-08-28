"""Local OCR helpers returning stable, machine-usable coordinates."""
from __future__ import annotations

import io
from PIL import Image
import pytesseract


def analyze(image_bytes: bytes, search_text: str = "") -> dict:
    image = Image.open(io.BytesIO(image_bytes))
    width, height = image.size
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    wanted = search_text.casefold() if search_text else ""
    elements = []
    for i, raw in enumerate(data["text"]):
        text = raw.strip()
        if not text or (wanted and wanted not in text.casefold()):
            continue
        confidence = float(data["conf"][i])
        if confidence < 30:
            continue
        x, y = int(data["left"][i]), int(data["top"][i])
        w, h = int(data["width"][i]), int(data["height"][i])
        cx, cy = x + w // 2, y + h // 2
        elements.append({
            "text": text, "confidence": round(confidence, 1),
            "pixel": [cx, cy], "box": [x, y, w, h],
            "x_pct": round(cx / width * 100, 1),
            "y_pct": round(cy / height * 100, 1),
        })
    elements.sort(key=lambda item: (-item["confidence"], item["y_pct"], item["x_pct"]))
    return {"width": width, "height": height, "elements": elements}
