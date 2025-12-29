import json
import re
from openai import OpenAI


def _strip_code_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("json"):
            t = t[4:].strip()
    return t


def make_openai_client(openai_api_key: str) -> OpenAI:
    return OpenAI(api_key=openai_api_key)


def generate_blog_post(
    client: OpenAI,
    model: str,
) -> dict:
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
            print(f"🧠 OpenAI 글 생성 시도: {model} (attempt {attempt})")
            resp = client.responses.create(model=model, input=prompt)
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


def generate_thumbnail_title(
    client: OpenAI,
    model: str,
    full_title: str,
) -> str:
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
    resp = client.responses.create(model=model, input=prompt)
    t = (resp.output_text or "").strip()
    t = re.sub(r"[\r\n]+", " ", t).strip()
    return t[:18].strip()
