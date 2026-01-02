from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import os


# =========================
# 1️⃣ 기존 to_square_1024 (복원)
# =========================
def to_square_1024(img_bytes: bytes) -> bytes:
    img = Image.open(BytesIO(img_bytes)).convert("RGB")
    w, h = img.size
    size = min(w, h)

    left = (w - size) // 2
    top = (h - size) // 2
    img = img.crop((left, top, left + size, top + size))
    img = img.resize((1024, 1024), Image.LANCZOS)

    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# =========================
# 2️⃣ 한글 폰트 로딩
# =========================
KOREAN_FONT_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.otf",
]


def _load_korean_font(size: int):
    for path in KOREAN_FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


# =========================
# 3️⃣ 썸네일 타이틀 오버레이
# =========================
def add_title_to_image(img_bytes: bytes, title: str) -> bytes:
    img = Image.open(BytesIO(img_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)

    W, H = img.size
    bar_h = int(H * 0.22)

    bar = Image.new("RGBA", (W, bar_h), (0, 0, 0, 140))
    img.paste(bar, (0, H - bar_h), bar)

    font_size = int(bar_h * 0.38)
    font = _load_korean_font(font_size)

    text = title.strip()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    x = (W - tw) // 2
    y = H - bar_h + (bar_h - th) // 2

    draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=(255, 255, 255))

    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
