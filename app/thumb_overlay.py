from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import os


KOREAN_FONT_PATHS = [
    # GitHub Actions (Ubuntu)
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    # 로컬 / 기타
    "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.otf",
]


def _load_korean_font(size: int):
    for path in KOREAN_FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    # ❌ fallback (한글 깨짐 방지용 최후 수단)
    return ImageFont.load_default()


def add_title_to_image(img_bytes: bytes, title: str) -> bytes:
    img = Image.open(BytesIO(img_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)

    W, H = img.size
    bar_h = int(H * 0.22)

    # 하단 반투명 바
    bar = Image.new("RGBA", (W, bar_h), (0, 0, 0, 140))
    img.paste(bar, (0, H - bar_h), bar)

    font_size = int(bar_h * 0.38)
    font = _load_korean_font(font_size)

    text = title.strip()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    x = (W - tw) // 2
    y = H - bar_h + (bar_h - th) // 2

    # 그림자
    draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0))
    # 본문
    draw.text((x, y), text, font=font, fill=(255, 255, 255))

    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
