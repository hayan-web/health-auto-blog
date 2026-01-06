# app/formatter_v2.py
from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional


def _env(key: str, default: str = "") -> str:
    import os
    return (os.getenv(key) or default).strip()


def _escape(s: str) -> str:
    return html.escape(s or "", quote=False)


def _inline_markup(text: str) -> str:
    """
    - 모델이 본문에서 **강조** 를 쓰면 -> 색/굵기 span으로 변환
    - 나머지는 안전하게 escape
    """
    raw = text or ""
    esc = _escape(raw)

    # **bold** -> highlight
    esc = re.sub(
        r"\*\*(.+?)\*\*",
        r"<span class='hl'>\1</span>",
        esc,
        flags=re.DOTALL,
    )
    return esc


def _ads_slot(slot: str) -> str:
    """
    수동 광고 코드(애드센스)를 env로 넣고 싶으면:
      ADSENSE_MANUAL_TOP / MID / BOTTOM
    가 비어있으면 슬롯 마커만 남겨둠(나중에 치환 가능).
    """
    mapping = {
        "top": _env("ADSENSE_MANUAL_TOP", ""),
        "mid": _env("ADSENSE_MANUAL_MID", ""),
        "bottom": _env("ADSENSE_MANUAL_BOTTOM", ""),
    }
    code = mapping.get(slot, "")
    if code:
        return f"<div class='ad ad-{slot}'>{code}</div>"
    return f"<!-- ADSENSE:{slot.upper()} -->"


def _h2(title: str) -> str:
    return f"""
<h2 class="sec-title">
  <span>{_escape(title)}</span>
</h2>
""".strip()


def _p(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    return f"<p class='para'>{_inline_markup(t)}</p>"


def _ul(items: Optional[List[str]]) -> str:
    if not items:
        return ""
    lis = []
    for it in items:
        it = (it or "").strip()
        if not it:
            continue
        lis.append(f"<li>{_inline_markup(it)}</li>")
    if not lis:
        return ""
    return "<ul class='bullets'>" + "\n".join(lis) + "</ul>"


def _img(url: str, alt: str = "") -> str:
    if not url:
        return ""
    return f"""
<div class="img-wrap">
  <img src="{_escape(url)}" alt="{_escape(alt)}" loading="lazy" />
</div>
""".strip()


def format_post_v2(
    *,
    title: str,
    keyword: str,
    hero_url: str,
    body_url: str,
    disclosure_html: str = "",
    summary_bullets: Optional[List[str]] = None,
    sections: Optional[List[Dict[str, Any]]] = None,
    warning_bullets: Optional[List[str]] = None,
    checklist_bullets: Optional[List[str]] = None,
    outro: str = "",
) -> str:
    """
    sections 예시:
      [{"title":"...", "body":["문단1","문단2"], "bullets":["...","..."]}, ...]
    """
    sections = sections or []
    # 최소 3개 섹션으로 맞추기(없으면 빈 값 채워서라도 틀 유지)
    while len(sections) < 3:
        sections.append({"title": "", "body": [], "bullets": []})

    # 섹션 정리
    def sec_title(i: int) -> str:
        t = (sections[i].get("title") or "").strip()
        if t:
            return t
        # 제목이 비면 키워드 기반 기본값
        base = ["핵심 정리", "실전 팁", "체크리스트/주의점"]
        return f"{keyword} {base[i] if i < len(base) else '정리'}".strip()

    def sec_body(i: int) -> List[str]:
        b = sections[i].get("body")
        if isinstance(b, list):
            return [str(x) for x in b if str(x).strip()]
        # 문자열로 오는 케이스 방어
        if isinstance(b, str) and b.strip():
            return [b.strip()]
        return []

    def sec_bullets(i: int) -> List[str]:
        bl = sections[i].get("bullets")
        if isinstance(bl, list):
            return [str(x) for x in bl if str(x).strip()]
        return []

    # 스타일(CSS)
    css = """
<style>
  .wrap { max-width: 760px; margin: 0 auto; padding: 18px 14px; font-family: 'Malgun Gothic','Apple SD Gothic Neo',sans-serif; line-height: 1.8; color:#111827; }
  h1.title { font-size: 30px; font-weight: 900; margin: 6px 0 14px; letter-spacing:-0.4px; }
  .meta { color:#6b7280; font-size:13px; margin-bottom:12px; }
  .sec-title { margin: 22px 0 10px; padding: 10px 12px; border-left: 6px solid #111827; background: #f3f4f6; border-radius: 10px; font-size: 18px; font-weight: 900; }
  .para { margin: 0 0 12px; font-size: 16px; }
  .bullets { margin: 10px 0 16px 18px; }
  .bullets li { margin: 6px 0; }
  .img-wrap { margin: 16px 0; }
  .img-wrap img { width: 100%; border-radius: 14px; box-shadow: 0 6px 18px rgba(0,0,0,0.10); display:block; }
  .summary { background:#ecfeff; border:1px solid #a5f3fc; border-radius:14px; padding:14px 14px; margin: 12px 0 16px; }
  .summary h3 { margin: 0 0 8px; font-size: 16px; font-weight: 900; }
  .disclosure { background:#fff7ed; border:1px solid #fed7aa; color:#9a3412; border-radius:14px; padding:12px 14px; margin: 10px 0 14px; }
  .disclosure strong { font-weight: 900; }
  .hl { color:#0ea5e9; font-weight: 900; }
  .ad { margin: 18px 0; padding: 10px; border: 1px dashed #e5e7eb; border-radius: 12px; background:#fff; }
</style>
""".strip()

    # 상단 대가성 문구(있으면)
    disclosure_block = ""
    if disclosure_html:
        disclosure_block = f"<div class='disclosure'>{disclosure_html}</div>"

    # 요약 블록
    summary_block = ""
    if summary_bullets:
        summary_block = f"""
<div class="summary">
  <h3>✅ 본글 요약</h3>
  {_ul(summary_bullets)}
</div>
""".strip()

    # 경고/체크리스트를 섹션3 아래에 합쳐서 붙이고 싶으면(선택)
    extra_guide = ""
    if warning_bullets:
        extra_guide += _h2("주의할 점")
        extra_guide += _ul(warning_bullets)
    if checklist_bullets:
        extra_guide += _h2("바로 실행 체크리스트")
        extra_guide += _ul(checklist_bullets)

    # 본문 구성(요청하신 순서 고정)
    html_out = f"""
<!-- wp:html -->
{css}
<div class="wrap">
  <h1 class="title">{_escape(title)}</h1>
  <div class="meta">카테고리/형식 분리 · 키워드: <span class="hl">{_escape(keyword)}</span></div>

  {disclosure_block}

  {_ads_slot("top")}

  {summary_block}

  {_img(hero_url, alt=title)}

  {_h2(sec_title(0))}
  {_ul(sec_bullets(0))}
  {''.join(_p(x) for x in sec_body(0))}

  {_h2(sec_title(1))}
  {_ul(sec_bullets(1))}
  {''.join(_p(x) for x in sec_body(1))}

  {_ads_slot("mid")}

  {_img(body_url, alt=f"{title} 관련 이미지")}

  {_h2(sec_title(2))}
  {_ul(sec_bullets(2))}
  {''.join(_p(x) for x in sec_body(2))}

  {extra_guide}

  {_ads_slot("bottom")}

  {(_p(outro) if outro else "")}
</div>
<!-- /wp:html -->
""".strip()

    return html_out
