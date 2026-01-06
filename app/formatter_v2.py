from __future__ import annotations

import html
import os
import re
from typing import List, Optional, Sequence


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _escape(s: str) -> str:
    return html.escape(s or "", quote=False)


def _bold_to_color(text: str) -> str:
    """
    사용자가 강조하고 싶은 단어를 LLM이 **굵게**로 찍으면,
    프론트에서 색+굵게로 보이도록 변환합니다.
    """
    if not text:
        return ""
    safe = _escape(text)

    # **...** -> span 강조
    safe = re.sub(
        r"\*\*(.+?)\*\*",
        r'<span style="color:#2563eb;font-weight:800;">\1</span>',
        safe,
    )
    return safe


def _render_bullets(items: Optional[Sequence[str]]) -> str:
    arr = [x.strip() for x in (items or []) if isinstance(x, str) and x.strip()]
    if not arr:
        return ""
    lis = "\n".join(f"<li>{_bold_to_color(x)}</li>" for x in arr)
    return f"<ul>\n{lis}\n</ul>"


def _ad_block(kind: str) -> str:
    """
    수동 광고:
    - ADSENSE_TOP / ADSENSE_MID / ADSENSE_BOTTOM 에 코드나 쇼트코드를 넣으면 그대로 들어갑니다.
    - 예: [adinserter block="1"]
    """
    key = {"top": "ADSENSE_TOP", "mid": "ADSENSE_MID", "bottom": "ADSENSE_BOTTOM"}.get(kind, "")
    code = _env(key, "")
    if not code:
        return ""
    # WP가 쇼트코드/스크립트를 처리하도록 escape 하지 않습니다.
    return f"""
<div class="adsense-manual adsense-{kind}">
{code}
</div>
""".strip()


def _h2(title: str) -> str:
    t = _escape(title)
    # style은 WP에서 허용되는 경우가 많고, 허용 안 돼도 h2 자체는 렌더됩니다.
    return f"""
<h2 style="margin:34px 0 12px; padding:12px 14px; border-left:6px solid #16a34a; background:#f0fdf4; border-radius:12px; font-size:20px; line-height:1.35;">
{t}
</h2>
""".strip()


def _para(text: str) -> str:
    t = _bold_to_color(text)
    if not t:
        return ""
    return f"<p style='margin:0 0 14px; font-size:17px; line-height:1.85; color:#111827;'>{t}</p>"


def format_post_v2(
    *,
    title: str,
    keyword: str,
    hero_url: str,
    body_url: str,
    disclosure_html: str = "",
    summary_bullets: Optional[List[str]] = None,
    sections: Optional[list] = None,
    warning_bullets: Optional[List[str]] = None,
    checklist_bullets: Optional[List[str]] = None,
    outro: Optional[str] = None,
):
    """
    main.py에서 _as_html()로 감싸 쓰고 있으니 문자열 반환하면 됩니다.
    """
    sections = sections or []
    # 섹션 3개 기준으로 우선 배치(더 많으면 뒤로 이어붙임)
    sec_titles: List[str] = []
    sec_bodies: List[str] = []

    for it in sections:
        if isinstance(it, dict):
            h = (it.get("title") or it.get("heading") or it.get("h2") or "").strip()
            b = (it.get("body") or it.get("content") or "").strip()
            if h and b:
                sec_titles.append(h)
                sec_bodies.append(b)
        elif isinstance(it, (list, tuple)) and len(it) >= 2:
            h = str(it[0] or "").strip()
            b = str(it[1] or "").strip()
            if h and b:
                sec_titles.append(h)
                sec_bodies.append(b)

    # 요약
    summary_html = ""
    if summary_bullets:
        summary_html = f"""
<div style="margin:18px 0 8px;">
  <div style="padding:14px 14px; border:1px solid #e5e7eb; border-radius:14px; background:#ffffff;">
    <p style="margin:0 0 10px; font-weight:800; font-size:16px;">📌 본문 요약</p>
    {_render_bullets(summary_bullets)}
  </div>
</div>
""".strip()

    # 히어로 이미지(요약 다음)
    hero_html = f"""
<div style="margin:18px 0 22px;">
  <img src="{hero_url}" alt="{_escape(title)}" style="width:100%; border-radius:16px; box-shadow:0 6px 18px rgba(0,0,0,0.10);" />
</div>
""".strip()

    # 경고/체크리스트(있을 때만)
    warn_html = ""
    if warning_bullets:
        warn_html = f"""
<div style="margin:18px 0;">
  <div style="padding:14px 14px; border-radius:14px; background:#fff7ed; border:1px solid #fed7aa;">
    <p style="margin:0 0 10px; font-weight:800;">⚠️ 주의</p>
    {_render_bullets(warning_bullets)}
  </div>
</div>
""".strip()

    checklist_html = ""
    if checklist_bullets:
        checklist_html = f"""
<div style="margin:18px 0;">
  <div style="padding:14px 14px; border-radius:14px; background:#eff6ff; border:1px solid #bfdbfe;">
    <p style="margin:0 0 10px; font-weight:800;">✅ 체크리스트</p>
    {_render_bullets(checklist_bullets)}
  </div>
</div>
""".strip()

    # 본문 구성(요청하신 포맷 고정)
    parts: List[str] = []
    if disclosure_html:
        parts.append(disclosure_html)

    parts.append(_ad_block("top"))          # 2. 에드센스 수동광고(상단)
    parts.append(summary_html)              # 3. 본글 요약
    parts.append(hero_html)                 # 4. 이미지(히어로)

    # 섹션 1~N
    for idx, (h, b) in enumerate(zip(sec_titles, sec_bodies)):
        if idx == 2:
            parts.append(_ad_block("mid"))  # 9. 에드센스 수동광고(중간) - 3번째 섹션 앞
        parts.append(_h2(h))
        # 본문은 여러 문단일 수 있으니 줄바꿈 기준으로 p 분리
        for para in [x.strip() for x in b.split("\n") if x.strip()]:
            parts.append(_para(para))

        # 중간 이미지(원하시면 2번째 섹션 끝에 넣기)
        if idx == 1 and body_url:
            parts.append(f"""
<div style="margin:22px 0;">
  <img src="{body_url}" alt="{_escape(title)} 관련 이미지" style="width:100%; border-radius:16px; box-shadow:0 6px 18px rgba(0,0,0,0.08);" />
</div>
""".strip())

    parts.append(warn_html)
    parts.append(checklist_html)

    if outro:
        parts.append(_h2("마무리"))
        for para in [x.strip() for x in str(outro).split("\n") if x.strip()]:
            parts.append(_para(para))

    parts.append(_ad_block("bottom"))       # 12. 에드센스 수동광고(하단)

    final = "\n".join([p for p in parts if p and p.strip()])

    return f"""
<div style="font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;">
{final}
</div>
""".strip()
