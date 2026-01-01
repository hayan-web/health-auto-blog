# app/formatter_v2.py
from __future__ import annotations

from typing import Any, List, Dict


def _esc(s: str) -> str:
    # 아주 단순한 escape(필요 최소)
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _p(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    return f"<p class='p'>{_esc(t)}</p>"


def _li(items: List[str]) -> str:
    items = [i.strip() for i in (items or []) if (i or "").strip()]
    if not items:
        return ""
    lis = "".join([f"<li>{_esc(x)}</li>" for x in items])
    return f"<ul class='ul'>{lis}</ul>"


def _card(title: str, body: str | None = None, bullets: List[str] | None = None) -> str:
    body_html = _p(body or "")
    bullets_html = _li(bullets or [])
    return f"""
    <section class="card">
      <div class="card-title">✅ {_esc(title)}</div>
      <div class="card-body">
        {body_html}
        {bullets_html}
      </div>
    </section>
    """.strip()


def _warning_box(title: str, bullets: List[str]) -> str:
    bullets_html = _li(bullets)
    return f"""
    <section class="warn">
      <div class="warn-title">⚠️ {_esc(title)}</div>
      <div class="warn-body">{bullets_html}</div>
    </section>
    """.strip()


def _summary_box(keyword: str, bullets: List[str]) -> str:
    bullets_html = _li(bullets)
    return f"""
    <section class="summary">
      <div class="summary-title">📌 1분 요약 ({_esc(keyword)})</div>
      <div class="summary-body">{bullets_html}</div>
    </section>
    """.strip()


def format_post_v2(
    *,
    title: str,
    keyword: str,
    hero_url: str,
    body_url: str,
    disclosure_html: str = "",
    summary_bullets: List[str] | None = None,
    sections: List[Dict[str, Any]] | None = None,
    warning_bullets: List[str] | None = None,
    checklist_bullets: List[str] | None = None,
    outro: str = "",
) -> str:
    """
    - 상단 대표 이미지 1장 + (중간) 이미지 1장 포함
    - '캡처 레퍼런스'처럼: 요약박스/카드/주의박스/체크리스트형
    - 애드센스 수동광고 슬롯 3개 마커 포함:
        1) 요약박스 위
        2) 첫 카드(소제목카드) 위
        3) 맨 아래
    """
    summary_bullets = summary_bullets or [
        "오늘 바로 할 수 있는 관리법 3가지만 기억하세요",
        "증상이 지속되면 병원 상담이 우선입니다",
        "생활습관/운동/식단을 한 번에 정리했습니다",
    ]

    sections = sections or []
    warning_bullets = warning_bullets or [
        "갑작스러운 극심한 통증, 호흡곤란, 식은땀/어지럼이 동반되면 즉시 진료가 필요합니다",
        "기저질환(심장/폐질환)이 있으면 자가판단을 피하세요",
    ]
    checklist_bullets = checklist_bullets or [
        "무리하지 않는 선에서 10~20분 가벼운 걷기부터",
        "수면/카페인/음주 패턴 점검",
        "통증/증상 기록(언제, 얼마나, 무엇을 할 때?)",
    ]

    # ✅ 본문 CSS(테마 영향 최소로 '클래스' 위주)
    css = """
<style>
  .wrap{font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif; line-height:1.85; color:#222; letter-spacing:-0.2px;}
  .hero{margin:0 0 18px;}
  .hero img{width:100%; max-width:900px; display:block; margin:0 auto; border-radius:16px; box-shadow:0 8px 24px rgba(0,0,0,.10);}
  .disclosure{background:#fff3f3; border:1px solid #ffd3d3; color:#b30000; padding:12px 14px; border-radius:12px; font-size:14px; margin:14px 0 18px;}
  .summary{background:#f3f8ff; border:1px solid #d8e9ff; padding:16px; border-radius:14px; margin:18px 0;}
  .summary-title{font-weight:800; font-size:18px; margin:0 0 10px;}
  .card{background:#ffffff; border:1px solid #e7ecf2; border-radius:14px; padding:16px; margin:16px 0; box-shadow:0 6px 18px rgba(0,0,0,.06);}
  .card-title{font-weight:800; font-size:17px; margin:0 0 10px;}
  .p{margin:0 0 12px; font-size:16.5px;}
  .ul{margin:0; padding-left:18px;}
  .ul li{margin:8px 0; font-size:16.5px;}
  .midimg{margin:22px 0;}
  .midimg img{width:100%; max-width:900px; display:block; margin:0 auto; border-radius:16px; box-shadow:0 8px 22px rgba(0,0,0,.10);}
  .warn{background:#fff7e8; border:1px solid #ffe0a8; padding:16px; border-radius:14px; margin:18px 0;}
  .warn-title{font-weight:900; font-size:17px; margin:0 0 10px;}
  .check{background:#f2fff6; border:1px solid #c9f1d6; padding:16px; border-radius:14px; margin:18px 0;}
  .check-title{font-weight:900; font-size:17px; margin:0 0 10px;}
  .ads{margin:18px 0; display:block;}
</style>
""".strip()

    # ✅ 애드센스 슬롯 마커(나중에 inject에서 치환)
    ad_top = "<div class='ads'><!--AD_SLOT_TOP--></div>"
    ad_mid = "<div class='ads'><!--AD_SLOT_MID--></div>"
    ad_bottom = "<div class='ads'><!--AD_SLOT_BOTTOM--></div>"

    disclosure_block = f"<div class='disclosure'>{disclosure_html}</div>" if disclosure_html.strip() else ""

    summary = _summary_box(keyword, summary_bullets)

    # 카드 섹션 구성
    cards_html = []
    for s in sections:
        st = (s.get("title") or "").strip()
        sb = (s.get("body") or "").strip()
        bullets = s.get("bullets") or s.get("points") or []
        if not st:
            continue
        cards_html.append(_card(st, sb, bullets))

    if not cards_html:
        # fallback: 카드 3개를 강제로 만들어 “줄글” 방지
        cards_html = [
            _card("원인으로 자주 나오는 경우", "가장 흔한 케이스부터 정리합니다.", ["근육/자세/과사용", "위장/역류성 증상", "스트레스/과호흡"]),
            _card("집에서 해볼 수 있는 관리", "무리 없는 선에서 우선순위만 잡습니다.", ["온찜질/가벼운 스트레칭", "카페인/음주 줄이기", "수면 리듬 고정"]),
            _card("병원 가야 하는 신호", "아래 신호가 있으면 지체하지 마세요.", ["호흡곤란/식은땀", "갑자기 심해지는 통증", "기저질환 동반"]),
        ]

    warn = _warning_box("이런 증상은 병원 우선", warning_bullets)

    checklist = f"""
    <section class="check">
      <div class="check-title">✅ 오늘의 체크리스트</div>
      {_li(checklist_bullets)}
    </section>
    """.strip()

    outro_html = _p(outro) if outro.strip() else ""

    # ✅ 상단/중간 이미지 포함(문서 안에서 “딱 2장”만)
    hero = f"""
    <div class="hero">
      <img src="{hero_url}" alt="{_esc(title)}" />
    </div>
    """.strip()

    midimg = f"""
    <div class="midimg">
      <img src="{body_url}" alt="{_esc(title)} 관련 이미지" />
    </div>
    """.strip()

    html = f"""
{css}
<div class="wrap">
  {disclosure_block}
  {hero}

  {ad_top}
  {summary}

  {ad_mid}
  {''.join(cards_html)}

  {midimg}

  {warn}
  {checklist}
  {outro_html}

  {ad_bottom}
</div>
""".strip()

    return html
