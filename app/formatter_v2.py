# app/formatter_v2.py
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def _escape_html(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s


def _build_highlighter(terms: List[str], max_hits_total: int = 24):
    """
    terms에 포함된 단어를 <span class="hl">로 감싸 강조합니다.
    너무 과도해지지 않도록 전체 히트 수 제한.
    """
    terms = [t.strip() for t in terms if isinstance(t, str) and t.strip()]
    # 너무 짧은 글자/중복 제거
    uniq: List[str] = []
    for t in terms:
        if len(t) < 2:
            continue
        if t not in uniq:
            uniq.append(t)
    if not uniq:
        return lambda x: x

    # 긴 단어 우선 매칭
    uniq.sort(key=len, reverse=True)
    pat = re.compile("(" + "|".join(map(re.escape, uniq)) + ")")

    hit = {"n": 0}

    def apply(text: str) -> str:
        if not text:
            return ""
        def repl(m):
            if hit["n"] >= max_hits_total:
                return m.group(0)
            hit["n"] += 1
            return f'<span class="hl">{m.group(0)}</span>'
        return pat.sub(repl, text)

    return apply


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
    highlight_terms: Optional[List[str]] = None,
    # ✅ 쿠팡/추가 블록을 “HTML로” 주입(본문에 코드 튀는 문제 방지)
    extra_top_html: str = "",
    extra_mid_html: str = "",
    extra_bottom_html: str = "",
) -> str:
    title = (title or "").strip()
    keyword = (keyword or "").strip()

    summary_bullets = summary_bullets or []
    sections = sections or []
    warning_bullets = warning_bullets or []
    checklist_bullets = checklist_bullets or []

    # highlight terms: AI가 준 값 우선, 없으면 키워드 토큰 일부 사용
    ht = []
    if highlight_terms:
        ht = [x for x in highlight_terms if isinstance(x, str)]
    if not ht:
        ht = [t for t in re.split(r"\s+", keyword) if len(t) >= 2][:6]

    highlighter = _build_highlighter(ht, max_hits_total=22)

    def P(text: str) -> str:
        t = _escape_html(text)
        t = highlighter(t)
        return f"<p class='p'>{t}</p>"

    def LI(text: str) -> str:
        t = _escape_html(text)
        t = highlighter(t)
        return f"<li>{t}</li>"

    # sections normalize
    norm_sections: List[Dict[str, Any]] = []
    for s in sections:
        if not isinstance(s, dict):
            continue
        h2 = (s.get("h2") or s.get("title") or "").strip()
        paras = s.get("paras") or s.get("paragraphs") or []
        bullets = s.get("bullets") or []
        if isinstance(paras, str):
            paras = [paras]
        if isinstance(bullets, str):
            bullets = [bullets]
        norm_sections.append({"h2": h2, "paras": paras, "bullets": bullets})

    # ✅ 요청하신 글 순서
    # 1. 제목
    # 2. 에드센스 수동광고
    # 3. 본글 요약
    # 4. 이미지
    # 5. 소제목/본문1
    # 7. 소제목/본문2
    # 9. 에드센스 수동광고
    # 10. 소제목/본문3
    # 12. 에드센스 수동광고
    # (쿠팡글도 이 틀을 크게 벗어나지 않게 extra_* 로 끼워 넣음)

    # Ads placeholders (수동광고)
    ADS_TOP = "<div class='ad ad-top'>[ADSENSE_MANUAL_TOP]</div>"
    ADS_MID = "<div class='ad ad-mid'>[ADSENSE_MANUAL_MID]</div>"
    ADS_BOT = "<div class='ad ad-bot'>[ADSENSE_MANUAL_BOT]</div>"

    # Summary box
    summary_html = ""
    if summary_bullets:
        summary_html = f"""
<div class="box box-summary">
  <div class="box-title">요약</div>
  <ul class="ul">
    {''.join(LI(x) for x in summary_bullets[:6])}
  </ul>
</div>
""".strip()

    # Disclosure
    disclosure_block = ""
    if disclosure_html:
        disclosure_block = f"""
<div class="box box-disclosure">
  {disclosure_html}
</div>
""".strip()

    # Images
    hero_img = ""
    if hero_url:
        hero_img = f"""
<div class="img-wrap">
  <img src="{_escape_html(hero_url)}" alt="{_escape_html(title)}" />
</div>
""".strip()

    body_img = ""
    if body_url:
        body_img = f"""
<div class="img-wrap">
  <img src="{_escape_html(body_url)}" alt="{_escape_html(title)} 관련 이미지" />
</div>
""".strip()

    def render_section(sec: Dict[str, Any]) -> str:
        h2 = sec.get("h2") or ""
        paras = sec.get("paras") or []
        bullets = sec.get("bullets") or []
        h2_html = ""
        if h2:
            h2_html = f"<h2 class='h2'>{_escape_html(h2)}</h2>"
        paras_html = "".join(P(x) for x in paras if isinstance(x, str) and x.strip())
        bullets_html = ""
        bl = [x for x in bullets if isinstance(x, str) and x.strip()]
        if bl:
            bullets_html = f"<ul class='ul'>{''.join(LI(x) for x in bl[:8])}</ul>"
        return f"<section class='sec'>{h2_html}{paras_html}{bullets_html}</section>"

    # pick up to 3 sections
    s1 = norm_sections[0] if len(norm_sections) >= 1 else {"h2": "핵심 정리", "paras": [], "bullets": []}
    s2 = norm_sections[1] if len(norm_sections) >= 2 else {"h2": "실전 팁", "paras": [], "bullets": []}
    s3 = norm_sections[2] if len(norm_sections) >= 3 else {"h2": "체크 포인트", "paras": [], "bullets": []}

    # Warning/Checklist/Outro (원하면 섹션 뒤에 붙여도 됨)
    extra_boxes = ""
    if warning_bullets:
        extra_boxes += f"""
<div class="box box-warn">
  <div class="box-title">주의할 점</div>
  <ul class="ul">{''.join(LI(x) for x in warning_bullets[:8])}</ul>
</div>
""".strip()
    if checklist_bullets:
        extra_boxes += f"""
<div class="box box-check">
  <div class="box-title">오늘의 체크리스트</div>
  <ul class="ul">{''.join(LI(x) for x in checklist_bullets[:10])}</ul>
</div>
""".strip()
    if outro:
        extra_boxes += f"<div class='outro'>{P(outro)}</div>"

    # ✅ 전체 HTML (WordPress가 “그대로” 렌더하도록 HTML 블록 하나로 묶음)
    html = f"""
<!-- wp:html -->
<div class="post-wrap">

<style>
.post-wrap {{
  max-width: 860px;
  margin: 0 auto;
  font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo","Malgun Gothic", Arial, sans-serif;
  line-height: 1.85;
  color: #111827;
}}
.post-title {{
  font-size: 30px;
  font-weight: 900;
  letter-spacing: -0.02em;
  margin: 10px 0 14px;
}}
.ad {{
  border: 1px dashed #cbd5e1;
  border-radius: 14px;
  padding: 18px;
  margin: 16px 0;
  background: #f8fafc;
  text-align: center;
  color: #64748b;
  font-weight: 800;
}}
.box {{
  border: 1px solid #e5e7eb;
  border-radius: 16px;
  padding: 14px 14px;
  margin: 16px 0;
  background: #ffffff;
}}
.box-title {{
  font-weight: 900;
  margin-bottom: 8px;
}}
.box-summary {{ background: #f0f9ff; border-color: #bae6fd; }}
.box-warn {{ background: #fff7ed; border-color: #fed7aa; }}
.box-check {{ background: #f0fdf4; border-color: #bbf7d0; }}
.box-disclosure {{ background: #fff7ed; border-color: #fed7aa; }}
.h2 {{
  margin: 24px 0 10px;
  padding: 12px 14px;
  border-radius: 14px;
  background: #111827;
  color: #ffffff;
  font-size: 18px;
  font-weight: 900;
}}
.p {{
  margin: 0 0 14px;
  font-size: 17px;
}}
.ul {{
  margin: 0;
  padding-left: 18px;
}}
.ul li {{
  margin: 6px 0;
  font-size: 16px;
}}
.img-wrap {{
  margin: 18px 0;
}}
.img-wrap img {{
  width: 100%;
  border-radius: 16px;
  box-shadow: 0 6px 18px rgba(0,0,0,0.12);
}}
.hl {{
  color: #0ea5e9; /* 강조 단어 색 */
  font-weight: 900;
}}
.sec {{
  margin: 6px 0 18px;
}}
.outro {{
  margin-top: 8px;
}}
/* 쿠팡 블록 클래스(테마 영향 줄이기 위해 기본만) */
.coupang-wrap {{
  border: 1px solid #e5e7eb;
  border-radius: 16px;
  padding: 14px;
  background: #f8fafc;
  margin: 16px 0;
}}
.coupang-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
}}
.coupang-card {{
  border: 1px solid #e5e7eb;
  border-radius: 14px;
  padding: 12px;
  background: #ffffff;
}}
.coupang-btn {{
  display: block;
  text-align: center;
  padding: 12px 14px;
  border-radius: 12px;
  background: #111827;
  color: #ffffff !important;
  text-decoration: none !important;
  font-weight: 900;
}}
.coupang-note {{
  color: #64748b;
  font-size: 12px;
  margin-top: 10px;
}}
</style>

<div class="post-title">{_escape_html(title)}</div>

{disclosure_block}

{ADS_TOP}

{summary_html}

{hero_img}

{extra_top_html}

{render_section(s1)}

{body_img}

{render_section(s2)}

{ADS_MID}

{extra_mid_html}

{render_section(s3)}

{extra_boxes}

{ADS_BOT}

{extra_bottom_html}

</div>
<!-- /wp:html -->
""".strip()

    return html
