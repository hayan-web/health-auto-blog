import os
import re
import json
import uuid
import textwrap
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
import requests
from openai import OpenAI
from google import genai
from google.genai import types

from app.config import Settings
from app.wp_client import upload_media_to_wp, publish_to_wp

S = Settings()

OPENAI_API_KEY = S.OPENAI_API_KEY
GOOGLE_API_KEY = S.GOOGLE_API_KEY

WP_URL = S.WP_URL
WP_USER = S.WP_USERNAME
WP_PW = S.WP_APP_PASSWORD

OPENAI_MODEL = S.OPENAI_MODEL
GEMINI_IMAGE_MODEL = S.GEMINI_IMAGE_MODEL

if not OPENAI_API_KEY:
    print("❌ OPENAI_API_KEY 누락")
    raise SystemExit(1)

if not GOOGLE_API_KEY:
    print("❌ GOOGLE_API_KEY 누락")
    raise SystemExit(1)

if not (WP_URL and WP_USER and WP_PW):
    print("❌ WP_URL / WP_USERNAME / WP_APP_PASSWORD 중 누락")
    raise SystemExit(1)

openai_client = OpenAI(api_key=OPENAI_API_KEY)
gemini_client = genai.Client(api_key=GOOGLE_API_KEY)

OPENAI_MODEL = "gpt-5-mini"
GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"


# =========================
# 1) Helpers
# =========================
def _strip_code_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("json"):
            t = t[4:].strip()
    return t


def _safe_slug_filename(name: str, fallback: str) -> str:
    s = (name or "").strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-zA-Z0-9가-힣\-_]", "", s)
    s = s[:60].strip("-") or fallback
    return s


# =========================
# 2) OpenAI (글 생성)
# =========================
def generate_blog_post() -> dict:
    prompt = """
당신은 한국어 블로그 글 작성 도우미입니다.

아래 형식의 JSON "객체(Object)" 로만 응답하세요.
- JSON 배열([]) 금지
- JSON 외 텍스트(설명/코드펜스/추가문장) 금지

출력 형식(키 3개 고정):
{
  "title": "제목",
  "content": "본문(문단은 \\n\\n 로 구분)",
  "img_prompt": "대표 이미지 생성용 프롬프트(영문 권장)"
}

작성 규칙:
- 제목 40~60자
- 본문 1500자 전후(±20%), 소제목 포함
- 과장/허위/의학적 단정 금지(일반 정보 수준)
- 문단은 \\n\\n 로 나눠 작성
- 마지막에 “참고하면 좋은 습관 3가지” 소제목 + 체크리스트 정리

주제:
40~50대에게 도움이 되는 건강관리 및 생활습관 실천 가이드
"""

    last_err = None
    for attempt in range(1, 3):
        try:
            print(f"🧠 OpenAI 글 생성 시도: {OPENAI_MODEL} (attempt {attempt})")
            resp = openai_client.responses.create(model=OPENAI_MODEL, input=prompt)
            text = _strip_code_fence(resp.output_text)
            data = json.loads(text)

            if not isinstance(data, dict):
                raise ValueError(f"JSON이 객체가 아닙니다: {type(data)}")

            if not data.get("title") or not data.get("content"):
                raise ValueError("JSON 필수 필드(title/content) 누락")

            if not data.get("img_prompt"):
                data["img_prompt"] = (
                    "health lifestyle illustration, korean middle-aged audience, "
                    "clean minimal, soft light, no text, watercolor, high clarity"
                )

            return data
        except Exception as e:
            last_err = e
            print(f"⚠️ OpenAI 글 생성 실패 (attempt {attempt}): {e}")

    raise RuntimeError(f"OpenAI 글 생성 최종 실패: {last_err}")


def generate_thumbnail_title(full_title: str) -> str:
    prompt = f"""
아래 블로그 제목을 보고,
썸네일 이미지에 넣을 짧은 제목을 만들어주세요.

조건:
- 10~16자 이내
- 핵심 키워드만 남기기
- 조사/부사 최소화
- 감탄사, 특수문자 금지
- 출력은 텍스트 한 줄만

원제목:
{full_title}
"""
    resp = openai_client.responses.create(model=OPENAI_MODEL, input=prompt)
    t = (resp.output_text or "").strip()
    t = re.sub(r"[\r\n]+", " ", t).strip()
    # 혹시 너무 길면 강제 컷(안전)
    return t[:18].strip()


# =========================
# 3) Gemini NanoBanana (이미지 생성)
# =========================
def generate_nanobanana_image_png_bytes(prompt: str) -> bytes:
    img_prompt = f"""
Create ONE single scene illustration for a blog thumbnail.
Hard constraints:
- square (1:1)
- SINGLE scene, SINGLE frame
- NO collage, NO triptych, NO split panels, NO multiple images
- NO grid, NO montage, NO storyboard
- centered subject, clean background
- no text, no watermark, no logo
Style: clean minimal, soft light, high clarity
Prompt: {prompt}
"""

    resp = gemini_client.models.generate_content(
        model=GEMINI_IMAGE_MODEL,
        contents=[img_prompt],
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )

    # candidates 경로(주로 여기)
    candidates = getattr(resp, "candidates", None)
    if candidates:
        for cand in candidates:
            content = getattr(cand, "content", None)
            if not content:
                continue
            parts = getattr(content, "parts", None) or []
            for part in parts:
                inline = getattr(part, "inline_data", None)
                if inline and getattr(inline, "data", None):
                    data = inline.data
                    if isinstance(data, (bytes, bytearray)):
                        return bytes(data)
                    if isinstance(data, str):
                        import base64
                        return base64.b64decode(data)

    # 혹시 resp.parts 형태로 오는 경우
    parts = getattr(resp, "parts", None)
    if parts:
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                data = inline.data
                if isinstance(data, (bytes, bytearray)):
                    return bytes(data)
                if isinstance(data, str):
                    import base64
                    return base64.b64decode(data)

    raise RuntimeError("Gemini 응답에서 이미지 데이터를 찾지 못했습니다.")


# =========================
# 4) Thumbnail 텍스트 오버레이
# =========================
def _load_font(size: int) -> ImageFont.FreeTypeFont:
    font_candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in font_candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def add_title_to_image(image_bytes: bytes, title: str) -> bytes:
    img = Image.open(BytesIO(image_bytes)).convert("RGBA")
    w, h = img.size

    draw = ImageDraw.Draw(img)

    # 하단 반투명 바(가독성)
    bar_h = int(h * 0.28)
    overlay = Image.new("RGBA", (w, bar_h), (0, 0, 0, 130))
    img.paste(overlay, (0, h - bar_h), overlay)

    font_size = max(28, int(w * 0.055))
    font = _load_font(font_size)

    # 너무 길면 자동 줄바꿈
    wrapped = textwrap.fill(title, width=10)

    # 텍스트 그림자 + 흰색 본문
    # (Pillow 버전 차이를 고려해 multiline_textbbox 우선)
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
        draw.multiline_text((x + dx, y + dy), wrapped, font=font, fill=(0, 0, 0, 180), align="center")

    draw.multiline_text((x, y), wrapped, font=font, fill=(255, 255, 255, 255), align="center")

    out = BytesIO()
    img.convert("RGB").save(out, format="PNG")
    return out.getvalue()
    
from PIL import Image
from io import BytesIO

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


# =========================
# 5) WordPress: Media Upload (RAW binary) + Post Publish
# =========================
def make_ascii_filename(prefix: str, ext: str = "png") -> str:
    """
    헤더에 넣어도 안전한 ASCII 파일명 생성 (한글/특수문자 절대 없음)
    """
    uid = uuid.uuid4().hex[:10]
    prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", (prefix or "img")).strip("-")
    if not prefix:
        prefix = "img"
    return f"{prefix}-{uid}.{ext}"


def force_ascii(s: str) -> str:
    """
    혹시라도 남아있는 비ASCII 제거
    """
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", (s or "file")).strip("-") or "file"

    top_html = f"""
<div style="margin-bottom:28px;">
  <img src="{hero_url}" alt="{data["title"]}" style="width:100%; border-radius:14px; box-shadow:0 4px 14px rgba(0,0,0,0.14);" />
</div>
"""

    mid_img_html = f"""
<div style="margin:28px 0;">
  <img src="{body_url}" alt="{data["title"]} 관련 이미지" style="width:100%; border-radius:14px; box-shadow:0 4px 14px rgba(0,0,0,0.12);" />
</div>
"""

    body_parts = []
    for i, p in enumerate(raw_paras):
        if i == 0:
            # 첫 문단 전에 이미 top 이미지가 있으니 그대로 문단부터
            pass
        if i == mid_idx:
            body_parts.append(mid_img_html)
        body_parts.append(ptag(p))

    final_html = f"""
{top_html}
<div style="line-height:1.9; font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;">
  {''.join(body_parts)}
</div>
"""

    api_endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    payload = {
        "title": data["title"],
        "content": final_html,
        "status": "publish",
        "featured_media": featured_media_id,
    }

    print("📝 POST ->", api_endpoint)
    print("📝 title ->", payload["title"][:80])

    res = requests.post(api_endpoint, auth=(WP_USER, WP_PW), json=payload, timeout=60)
    print("📝 WP status:", res.status_code)
    print("📝 WP resp:", (res.text or "")[:500])

    if res.status_code != 201:
        raise RuntimeError(f"워드프레스 글 발행 실패: {res.status_code} / {res.text}")

    return res.json()["id"]


# =========================
# 6) MAIN
# =========================
if __name__ == "__main__":
    try:
        # 1) 글 생성 (OpenAI)
        post = generate_blog_post()

        # 2) 썸네일용 짧은 타이틀 (OpenAI)
        thumb_title = generate_thumbnail_title(post["title"])
        print("🏷️ 썸네일 타이틀:", thumb_title)

        # 3) 이미지 2장 생성 (Gemini NanoBanana)
        print("🎨 Gemini 이미지(상단/대표) 생성 중...")
        hero_img = generate_nanobanana_image_png_bytes(post["img_prompt"])

        print("🎨 Gemini 이미지(중간) 생성 중...")
        body_img = generate_nanobanana_image_png_bytes(
            post["img_prompt"] + ", different composition, different angle, no text"
        )
        
        # ✅ 이미지 생성 직후 무조건 1:1 정사각 고정
        hero_img = to_square_1024(hero_img)
        body_img = to_square_1024(body_img)


        # 4) 대표 이미지에 타이틀 오버레이
        hero_img_titled = add_title_to_image(hero_img, thumb_title)

        # ✅ 오버레이 후에도 혹시 비율 깨질 수 있으니 다시 1:1 고정
        hero_img_titled = to_square_1024(hero_img_titled)


        # 5) WP 미디어 업로드(대표/중간)
        hero_name = make_ascii_filename("featured")
        body_name = make_ascii_filename("body")
        
        hero_url, hero_media_id = upload_media_to_wp(WP_URL, WP_USER, WP_PW, hero_img_titled, hero_name)
        body_url, _ = upload_media_to_wp(WP_URL, WP_USER, WP_PW, body_img, body_name)
        
        post_id = publish_to_wp(
            WP_URL, WP_USER, WP_PW,
            post, hero_url, body_url,
            featured_media_id=hero_media_id
        )

        print(f"✅ 완료! post_id={post_id}")

    except Exception as e:
        print(f"❌ 시스템 중단: {e}")
        raise
