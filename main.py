import os
import json
import requests
from openai import OpenAI


# ===== 1) 환경변수 로드 =====
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

WP_URL = os.getenv("WP_URL", "").strip().rstrip("/")
WP_USER = os.getenv("WP_USERNAME", "").strip()
WP_PW = os.getenv("WP_APP_PASSWORD", "").strip().replace(" ", "")

if not OPENAI_API_KEY:
    print("❌ 오류: OPENAI_API_KEY 누락")
    raise SystemExit(1)

if not (WP_URL and WP_USER and WP_PW):
    print("❌ 오류: WP_URL / WP_USERNAME / WP_APP_PASSWORD 중 누락")
    raise SystemExit(1)

client = OpenAI(api_key=OPENAI_API_KEY)


# ===== 2) OpenAI로 글 생성 =====
# 가성비 추천:
# - 기본: gpt-5-mini (품질/비용 밸런스)
# - 더 싼 옵션: gpt-5-nano
MODEL_CANDIDATES = ["gpt-5-mini", "gpt-5-nano"]


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
- 본문 1200~2000자, 소제목 포함
- 과장/허위/의학적 단정 금지(일반 정보 수준)
- 문단은 \\n\\n 로 나눠 작성

주제:
40~50대에게 도움이 되는 건강 블로그 글 1편 작성
"""

    last_err = None

    for model_name in MODEL_CANDIDATES:
        for attempt in range(1, 3):
            try:
                print(f"🧠 OpenAI 모델 시도: {model_name} (attempt {attempt})")

                resp = client.responses.create(
                    model=model_name,
                    input=prompt,
                )

                # Responses API 텍스트 추출 (안정적으로)
                text = resp.output_text
                text = _strip_code_fence(text)
                data = json.loads(text)

                if not isinstance(data, dict):
                    raise ValueError(f"JSON이 객체가 아닙니다: {type(data)}")

                if not data.get("title") or not data.get("content"):
                    raise ValueError("JSON 필수 필드(title/content) 누락")

                if not data.get("img_prompt"):
                    data["img_prompt"] = "health blog illustration, clean minimal, soft light, watercolor style"

                return data

            except Exception as e:
                last_err = e
                print(f"⚠️ 실패: {model_name} (attempt {attempt}) / {e}")

    raise RuntimeError(f"모든 모델 호출 실패: {last_err}")


# ===== 3) 이미지는 기존 NanoBanana(pollinations) 유지 =====
def get_nanobanana_image(prompt: str) -> str:
    style_tag = "nanobanana style, vibrant yet clean, artistic watercolor touch"
    encoded_prompt = requests.utils.quote(f"{prompt}, {style_tag}")
    return f"https://pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&model=nanobanana"


# ===== 4) 워드프레스 업로드 =====
def publish_to_wp(data, img_url):
    paragraphs = data["content"].split("\n")
    formatted_body = "".join(
        f"<p style='margin-bottom:1.6em; font-size:18px; color:#333;'>{p.strip()}</p>"
        for p in paragraphs
        if p.strip()
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
