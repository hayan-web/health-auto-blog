# app/ai_openai.py
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI


# ------------------------------------------------------------
# Client
# ------------------------------------------------------------
def make_openai_client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key)


# ------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------
def _strip_code_fence(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _safe_json_loads(text: str) -> Dict[str, Any]:
    t = _strip_code_fence(text)
    try:
        return json.loads(t)
    except Exception:
        # JSON 밖의 잡텍스트가 섞였을 때 마지막 보루로 {...}만 추출
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def _min_len_ok(s: str, n: int) -> bool:
    return isinstance(s, str) and len(s.strip()) >= n


def _is_str_list(x: Any) -> bool:
    return isinstance(x, list) and all(isinstance(i, str) for i in x)


def _normalize_post(p: Dict[str, Any]) -> Dict[str, Any]:
    # 키 누락/형식 이상을 최대한 정리 (quality.py에 넘기기 전 안전장치)
    p = dict(p or {})
    p["title"] = (p.get("title") or "").strip()
    p["img_prompt"] = (p.get("img_prompt") or "").strip()

    for k in ["intro", "outro"]:
        if k in p:
            p[k] = (p.get(k) or "").strip()

    # list[str] 정리
    for k in ["summary_bullets", "warning_bullets", "checklist_bullets"]:
        v = p.get(k)
        if not _is_str_list(v):
            p[k] = []

    # sections 정리
    secs = p.get("sections")
    if not isinstance(secs, list):
        p["sections"] = []
    else:
        norm_secs: List[Dict[str, Any]] = []
        for s in secs:
            if not isinstance(s, dict):
                continue
            norm_secs.append(
                {
                    "title": (s.get("title") or "").strip(),
                    "body": (s.get("body") or "").strip(),
                    "bullets": s.get("bullets") if _is_str_list(s.get("bullets")) else [],
                }
            )
        p["sections"] = norm_secs

    return p


def _quick_constraints_ok(p: Dict[str, Any]) -> bool:
    """
    quality.py보다 한 단계 앞에서 "최소조건"만 체크해서
    실패 시 repair로 바로 보내기 위한 내부 검사.
    """
    p = _normalize_post(p)

    if not _min_len_ok(p.get("title", ""), 10):
        return False

    # img_prompt는 1:1 힌트를 강제
    ip = (p.get("img_prompt") or "").lower()
    if ("square" not in ip) and ("1:1" not in ip):
        return False
    if any(w in ip for w in ["collage", "typography", "text overlay", "poster"]):
        return False

    if len(p.get("summary_bullets", [])) < 3:
        return False
    if len(p.get("warning_bullets", [])) < 2:
        return False
    if len(p.get("checklist_bullets", [])) < 3:
        return False

    secs = p.get("sections", [])
    if len(secs) < 5:
        return False

    # 핵심: 섹션 body 길이
    for s in secs[:5]:
        if not _min_len_ok(s.get("body", ""), 180):  # 내부 기준은 180으로 더 빡세게
            return False
        if len(s.get("bullets", [])) < 2:
            return False

    if not _min_len_ok(p.get("outro", ""), 60):
        return False

    return True


def _build_generation_prompt(keyword: str) -> str:
    """
    길이/구조를 강하게 강제해서 sections가 짧게 나오지 않게 합니다.
    """
    return f"""
당신은 한국어 건강 정보 블로그 글을 "구조화된 JSON"으로만 출력합니다.
절대 설명/문장/코드펜스/주석을 붙이지 말고 JSON만 출력하세요.

[주제 키워드]
- {keyword}

[필수 출력 JSON 스키마]
{{
  "keyword": "{keyword}",
  "title": "제목(10~60자, 과장/느낌표 남발 금지)",
  "img_prompt": "블로그 대표 삽화용 이미지 프롬프트(영문 권장). 반드시: single scene, no collage, no text, square 1:1 포함",
  "summary_bullets": ["요약1(짧게)", "요약2", "요약3", "요약4(선택)"],
  "sections": [
    {{
      "title": "소제목1(4~18자)",
      "body": "본문1(반드시 180~420자, 한 문단으로 너무 짧게 쓰지 말 것)",
      "bullets": ["핵심 포인트 1", "핵심 포인트 2", "핵심 포인트 3(선택)"]
    }},
    ... 총 5~7개 섹션
  ],
  "warning_bullets": ["주의1(2개 이상)", "주의2", "주의3(선택)"],
  "checklist_bullets": ["체크1(3개 이상)", "체크2", "체크3", "체크4(선택)"],
  "outro": "마무리(반드시 60~200자)"
}}

[강제 규칙]
1) sections는 반드시 5~7개.
2) 모든 sections[i].body는 반드시 180자 이상. (짧으면 실패로 간주)
3) img_prompt에는 반드시 아래 문구를 그대로 포함:
   - "single scene, no collage, no text, square 1:1"
4) 의학적 확정 진단/치료 지시처럼 쓰지 말고, 일반 정보 + '증상이 지속되면 전문가 상담' 톤 유지.
5) JSON만 출력.
""".strip()


def _build_repair_prompt(original_json: Dict[str, Any]) -> str:
    """
    짧게 나온 섹션들을 규격에 맞게 "확장/보정"만 하는 프롬프트.
    """
    original = json.dumps(original_json, ensure_ascii=False)
    return f"""
아래 JSON은 스키마는 맞지만 품질 기준을 만족하지 못했습니다.
"제목/키워드의 방향성은 유지"하면서, 기준에 맞게 확장/보정해서
"JSON만" 다시 출력하세요. (설명/코드펜스 금지)

[수정해야 하는 필수 기준]
- sections는 5~7개 유지
- 모든 sections[i].body는 반드시 180~420자
- 모든 sections[i].bullets는 최소 2개
- summary_bullets 최소 3개
- warning_bullets 최소 2개
- checklist_bullets 최소 3개
- outro 60~200자
- img_prompt에는 반드시 "single scene, no collage, no text, square 1:1" 포함
- JSON 외 텍스트 출력 금지

[입력 JSON]
{original}
""".strip()


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------
def generate_blog_post(client: OpenAI, model: str, keyword: str) -> Dict[str, Any]:
    """
    1) 생성 → 2) 내부 최소검사 → 3) 실패 시 repair 1회
    - temperature 파라미터는 아예 사용하지 않습니다(지원 안하는 모델 대비).
    """
    # 1) 생성
    prompt = _build_generation_prompt(keyword)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You output ONLY valid JSON. No extra text."},
            {"role": "user", "content": prompt},
        ],
    )

    text = resp.choices[0].message.content or ""
    post = _safe_json_loads(text)
    post = _normalize_post(post)

    # 2) 최소검사 통과면 바로 리턴
    if _quick_constraints_ok(post):
        return post

    # 3) 실패면 repair 1회 (여기가 성공률 핵심)
    repair_prompt = _build_repair_prompt(post)
    resp2 = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You output ONLY valid JSON. No extra text."},
            {"role": "user", "content": repair_prompt},
        ],
    )
    text2 = resp2.choices[0].message.content or ""
    post2 = _safe_json_loads(text2)
    post2 = _normalize_post(post2)
    return post2


def generate_thumbnail_title(client: OpenAI, model: str, title: str) -> str:
    """
    썸네일 오버레이용: 2~8단어 정도의 매우 짧은 문구.
    """
    title = (title or "").strip()
    prompt = f"""
다음 글 제목을 보고, 썸네일에 넣을 "짧은 문구"를 2~8단어로 만들어 주세요.
- 특수문자/괄호/따옴표 금지
- 너무 길면 안됨
- 출력은 문구 한 줄만

제목: {title}
""".strip()

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Output ONE short line in Korean. No extra text."},
            {"role": "user", "content": prompt},
        ],
    )
    out = (resp.choices[0].message.content or "").strip()
    out = re.sub(r"[\r\n]+", " ", out).strip()
    # 안전장치: 너무 길면 앞쪽만
    if len(out) > 18:
        out = out[:18].strip()
    return out
