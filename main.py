import os
import json
import requests
import google.generativeai as genai


# ===== 1) 환경변수 로드 =====
API_KEY = os.getenv("GOOGLE_API_KEY", "").strip()
WP_URL = os.getenv("WP_URL", "").strip().rstrip("/")
WP_USER = os.getenv("WP_USERNAME", "").strip()
WP_PW = os.getenv("WP_APP_PASSWORD", "").strip().replace(" ", "")

if not API_KEY:
    print("❌ 오류: GOOGLE_API_KEY를 찾을 수 없습니다.")
    raise SystemExit(1)

if not (WP_URL and WP_USER and WP_PW):
    print("❌ 오류: WP_URL / WP_USERNAME / WP_APP_PASSWORD 중 누락이 있습니다.")
    raise SystemExit(1)

genai.configure(api_key=API_KEY)


# ===== 2) Gemini 글 생성 (모델 폴백을 generate_content 단계에서 수행) =====
MODEL_CANDIDATES = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]


def _strip_code_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("json"):
            t = t[4:].strip()
    return t


def generate_blog():
    prompt = """
당신은 한국어 블로그 글 작성 도우미입니다.

아래 조건을 반드시 지키세요.
- 반드시 JSON 형식으로만 응답하세요
- JSON 외의 텍스트는 절대 출력하지 마세요

출력 형식:
{
  "title": "제목",
  "content": "본문(문단은 \\n\\n 로 구분)",
  "img_prompt": "대표 이미지 생성용 프롬프트(영문 권장)"
}

작성 규칙:
- 제목은 40~60자 내외
- 본문은 소제목 포함, 1200~2000자
- 과장/허위/의학적 단정 금지 (일반 정보 수준)
- 문단은 \\n\\n 로 나눠서 작성

주제:
40~50대에게 도움이 되는 건강 블로그 글 1편 작성
"""

    last_err = None

    for model_name in MODEL_CANDIDATES:
        try:
            print(f"🧠 Gemini 모델 시도: {model_name}")
            model = genai.GenerativeModel(model_name)

            response = model.generate_content(
                prompt,
                generation_config={
                    "response_mime_type": "application/json"
                }
            )

            text = (response.text or "").strip()

            if text.startswith("```"):
                text = text.strip("`").replace("json", "", 1).strip()

            data = json.loads(text)

            if not data.get("title") or not data.get("content"):
                raise ValueError("JSON 필수 필드 누락")

            if not data.get("img_prompt"):
                data["img_prompt"] = "health blog illustration, clean minimal, watercolor style"

            return data

        except Exception as e:
            last_err = e
            print(f"⚠️ 실패: {model_name} / {e}")

    raise RuntimeError(f"모든 모델 호출 실패: {last_err}")


# ===== 3) 이미지 URL 만들기(기존 pollinations 방식 유지) =====
def get_nanobanana_image(prompt: str) -> str:
    style_tag = "nanobanana style, vibrant yet clean, artistic watercolor touch"
    encoded_prompt = requests.utils.quote(f"{prompt}, {style_tag}")
    return f"https://pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&model=nanobanana"


# ===== 4) 워드프레스 업로드 =====
def publish_to_wp(data, img_url):
    paragraphs = data["content"].split("\n")
    formatted_body = "".join(
        [f"<p style='margin-bottom:1.6em; font-size:18px; color:#333;'>{p.strip()}</p>"
         for p in paragraphs if p.strip()]
    )

    final_html = f"""
<div style="margin-bottom:30px;">
  <img src="{img_url}" alt="{data["title"]}" style="width:100%; border-radius:15px; box-shadow:0 4px 15px rgba(0,0,0,0.15);" />
  <p style="text-align:right; font-size:13px; color:#888; margin-top:10px;">*Artistic Touch by NanoBanana</p>
</div>

<div style="line-height:1.9; font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;">
  {formatted_body}
</div>
"""

    api_endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    auth = (WP_USER, WP_PW)
    payload = {
        "title": data["title"],
        "content": final_html,
        "status": "publish",
    }

    # 디버그 로그(민감정보 제외)
    print("POST ->", api_endpoint)
    print("payload title ->", payload["title"][:80])

    res = requests.post(api_endpoint, auth=auth, json=payload, timeout=30)
    print("WP status:", res.status_code)
    print("WP resp:", (res.text or "")[:500])

    if res.status_code == 201:
        link = None
        try:
            link = res.json().get("link")
        except Exception:
            pass
        print(f"✅ 발행 성공! 링크: {link}")
    else:
        raise RuntimeError(f"워드프레스 업로드 실패: {res.status_code} / {res.text}")


if __name__ == "__main__":
    try:
        content_data = generate_blog()
        image_url = get_nanobanana_image(content_data["img_prompt"])
        publish_to_wp(content_data, image_url)
    except Exception as e:
        print(f"❌ 시스템 중단: {e}")
        raise
