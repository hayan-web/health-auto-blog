import os
import json
from io import BytesIO

import requests
from openai import OpenAI
from google import genai
from google.genai import types


# =========================
# 0) ENV
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()

WP_URL = os.getenv("WP_URL", "").strip().rstrip("/")
WP_USER = os.getenv("WP_USERNAME", "").strip()
WP_PW = os.getenv("WP_APP_PASSWORD", "").strip().replace(" ", "")

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


# =========================
# 1) OpenAI (글 생성)
# =========================
OPENAI_MODEL = "gpt-5-mini"


def _strip_code_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("json"):
            t = t[4:].strip()
    return t


def generate_blog_post():
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
- 마지막에 “참고하면 좋은 습관 3가지” 소제목을 넣고 체크리스트 형태로 정리

주제:
40~50대에게 도움이 되는 건강관리 및 생활습관 실천 가이드
"""

    # 2회까지 재시도(가끔 JSON 깨질 때 대비)
    last_err = None
    for attempt in range(1, 3):
        try:
            print(f"🧠 OpenAI 글 생성 시도: {OPENAI_MODEL} (attempt {attempt})")
            resp = openai_client.responses.create(
                model=OPENAI_MODEL,
                input=prompt,
            )
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


# =========================
# 2) Gemini Nano Banana (이미지 생성)
#    - Nano Banana = Gemini 2.5 Flash Image
# =========================
GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"


def generate_nanobanana_image_png_bytes(prompt: str) -> bytes:
    """
    Gemini 이미지 생성 결과에서 이미지 bytes를 추출해 반환
    (응답 포맷이 바뀌어도 최대한 견고하게)
    """
    img_prompt = f"""
Create a blog-friendly illustration.
Constraints:
- clean minimal composition
- soft light
- high clarity
- no text, no watermark text, no logo
- safe, neutral, informative vibe
Prompt: {prompt}
"""

    resp = gemini_client.models.generate_content(
        model=GEMINI_IMAGE_MODEL,
        contents=[img_prompt],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"]
        ),
    )

    # (1) candidates 경로(가장 흔함)
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
                    # SDK에 따라 bytes or base64-string
                    if isinstance(data, (bytes, bytearray)):
                        return bytes(data)
                    if isinstance(data, str):
                        import base64
                        return base64.b64decode(data)

    # (2) 혹시 resp.parts 경로
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
# 3) WordPress (미디어 업로드 + 글 발행)
# =========================
def upload_media_to_wp(image_bytes: bytes, filename: str) -> str:
    """
    WP 미디어로 업로드 후 source_url 반환
    """
    media_endpoint = f"{WP_URL}/wp-json/wp/v2/media"

    file_obj = BytesIO(image_bytes)
    files = {
        "file": (filename, file_obj, "image/png")
    }

    res = requests.post(
        media_endpoint,
        auth=(WP_USER, WP_PW),
        files=files,
        timeout=60,
    )

    print("🖼️ WP media status:", res.status_code)
    print("🖼️ WP media resp:", (res.text or "")[:300])

    if res.status_code not in (200, 201):
        raise RuntimeError(f"미디어 업로드 실패: {res.status_code} / {res.text}")

    return res.json()["source_url"]


def publish_to_wp(data: dict, img1_url: str, img2_url: str):
    """
    이미지 2장 포함해서 본문 HTML 생성 후 발행
    """
    paragraphs = data["content"].split("\n")
    formatted_body = "".join(
        f"<p style='margin-bottom:1.6em; font-size:18px; color:#333;'>{p.strip()}</p>"
        for p in paragraphs if p.strip()
    )

    final_html = f"""
<div style="margin-bottom:28px;">
  <img src="{img1_url}" alt="{data["title"]}" style="width:100%; border-radius:14px; box-shadow:0 4px 14px rgba(0,0,0,0.14);" />
</div>

<div style="line-height:1.9; font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;">
  {formatted_body}

  <div style="margin-top:28px;">
    <img src="{img2_url}" alt="{data["title"]} 관련 이미지" style="width:100%; border-radius:14px; box-shadow:0 4px 14px rgba(0,0,0,0.12);" />
  </div>
</div>
"""

    api_endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    payload = {
        "title": data["title"],
        "content": final_html,
        "status": "publish",
    }

    print("📝 POST ->", api_endpoint)
    print("📝 title ->", payload["title"][:80])

    res = requests.post(api_endpoint, auth=(WP_USER, WP_PW), json=payload, timeout=60)
    print("📝 WP status:", res.status_code)
    print("📝 WP resp:", (res.text or "")[:500])

    if res.status_code == 201:
        link = None
        try:
            link = res.json().get("link")
        except Exception:
            pass
        print(f"✅ 발행 성공! 링크: {link}")
    else:
        raise RuntimeError(f"워드프레스 글 발행 실패: {res.status_code} / {res.text}")


# =========================
# 4) MAIN
# =========================
if __name__ == "__main__":
    try:
        # 1) 글 생성 (OpenAI)
        post = generate_blog_post()

        # 2) 이미지 2장 생성 (Gemini Nano Banana)
        print("🎨 Gemini 이미지 1 생성 중...")
        img1 = generate_nanobanana_image_png_bytes(post["img_prompt"])
        print("🎨 Gemini 이미지 2 생성 중...")
        img2 = generate_nanobanana_image_png_bytes(post["img_prompt"] + ", different composition, different angle")

        # 3) WP 미디어 업로드
        img1_url = upload_media_to_wp(img1, "hero.png")
        img2_url = upload_media_to_wp(img2, "body.png")

        # 4) WP 글 발행(이미지 2장 포함)
        publish_to_wp(post, img1_url, img2_url)

    except Exception as e:
        print(f"❌ 시스템 중단: {e}")
        raise
