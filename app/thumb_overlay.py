from io import BytesIO
import textwrap

from PIL import Image, ImageDraw, ImageFont


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    # GitHub Actions ubuntu에서 한글 폰트 사용 가능하면 최우선
    font_candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    for path in font_candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def to_square_1024(image_bytes: bytes) -> bytes:
    """
    어떤 비율로 오든 중앙 기준으로 정사각 크롭 후 1024x1024로 고정
    """
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((1024, 1024), Image.LANCZOS)

    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def add_title_to_image(image_bytes: bytes, title: str) -> bytes:
    """
    하단 반투명 바 + 한글 타이틀 오버레이
    """
    img = Image.open(BytesIO(image_bytes)).convert("RGBA")
    w, h = img.size

    draw = ImageDraw.Draw(img)

    # 하단 반투명 바(가독성)
    bar_h = int(h * 0.28)
    overlay = Image.new("RGBA", (w, bar_h), (0, 0, 0, 130))
    img.paste(overlay, (0, h - bar_h), overlay)

    font_size = max(28, int(w * 0.055))
    font = _load_font(font_size)

    wrapped = textwrap.fill(title, width=10)

    # 텍스트 크기 계산 (Pillow 버전 차이 대응)
    try:
        bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, align="center")
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
    except Exception:
        text_w, text_h = draw.multiline_textsize(wrapped, font=font)

    x = (w - text_w) // 2
    y = h - bar_h + (bar_h - text_h) // 2

    # shadow
    for dx, dy in [(2, 2), (2, 0), (0, 2)]:
        draw.multiline_text(
            (x + dx, y + dy),
            wrapped,
            font=font,
            fill=(0, 0, 0, 180),
            align="center",
        )

    draw.multiline_text(
        (x, y),
        wrapped,
        font=font,
        fill=(255, 255, 255, 255),
        align="center",
    )

    out = BytesIO()
    img.convert("RGB").save(out, format="PNG")
    return out.getvalue()
