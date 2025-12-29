from __future__ import annotations
from html import escape
from typing import Any


def _h2_id(text: str) -> str:
    # 워드프레스 앵커용 id
    import re
    s = text.strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-가-힣]", "", s)
    return s[:60] or "section"


def _p(txt: str) -> str:
    txt = escape(txt).replace("\n", "<br/>")
    return f"<p class='p'>{txt}</p>"


def _ul(items: list[str]) -> str:
    lis = "".join([f"<li>{escape(x)}</li>" for x in items if x.strip()])
    return f"<ul class='ul'>{lis}</ul>"


def _box(kind: str, title: str, inner_html: str) -> str:
    # kind: info | tip | warn | danger
    cls = {
        "info": "box box-info",
        "tip": "box box-tip",
        "warn": "box box-warn",
        "danger": "box box-danger",
    }.get(kind, "box box-info")
    return f"""
<div class="{cls}">
  <div class="box-title">{escape(title)}</div>
  <div class="box-body">{inner_html}</div>
</div>
""".strip()


def _table(headers: list[str], rows: list[list[str]]) -> str:
    th = "".join([f"<th>{escape(h)}</th>" for h in headers])
    tr = ""
    for r in rows:
        tds = "".join([f"<td>{escape(str(c))}</td>" for c in r])
        tr += f"<tr>{tds}</tr>"
    return f"""
<div class="table-wrap">
  <table class="tbl">
    <thead><tr>{th}</tr></thead>
    <tbody>{tr}</tbody>
  </table>
</div>
""".strip()


def _faq(items: list[dict[str, str]]) -> str:
    blocks = []
    for qa in items:
        q = escape((qa.get("q") or "").strip())
        a = escape((qa.get("a") or "").strip()).replace("\n", "<br/>")
        if not q or not a:
            continue
        blocks.append(f"""
<details class="faq">
  <summary>{q}</summary>
  <div class="faq-a">{a}</div>
</details>
""".strip())
    return "\n".join(blocks)


def format_post_body(post: dict[str, Any], *, hero_url: str, body_url: str) -> str:
    """
    캡처 스타일로 본문 HTML 생성:
    - 상단 이미지(대표) 1장
    - 중간 이미지 1장
    - 요약박스 + 목차 + 섹션 + 박스/표 + FAQ + CTA
    """
    title = post.get("title", "")
    summary = post.get("summary_bullets") or []
    sections = post.get("sections") or []
    table = post.get("table") or {}
    faqs = post.get("faqs") or []
    cta = post.get("cta") or "오늘 내용이 도움이 되셨다면, 필요한 항목부터 하나만 실천해 보세요."

    # 1) CSS (인라인으로 넣어도 WP에서 잘 먹습니다)
    style = """
<style>
.p{margin:0 0 1.2em; font-size:17px; line-height:1.85; color:#222;}
.ul{margin:0 0 1.4em 1.2em; padding:0;}
.ul li{margin:.45em 0; line-height:1.75;}
.hr{margin:26px 0; border:0; border-top:1px solid #e9e9e9;}
.toc{padding:16px 18px; border:1px solid #e6e6e6; border-radius:14px; background:#fafafa; margin:18px 0 28px;}
.toc a{text-decoration:none;}
.box{border-radius:14px; padding:14px 16px; margin:18px 0;}
.box-title{font-weight:800; margin-bottom:8px;}
.box-body{line-height:1.75;}
.box-info{border:1px solid #cfe3ff; background:#f4f8ff;}
.box-tip{border:1px solid #cfeede; background:#f2fbf6;}
.box-warn{border:1px solid #ffe3b3; background:#fff8ea;}
.box-danger{border:1px solid #ffd0d0; background:#fff3f3;}
.table-wrap{overflow-x:auto; margin:18px 0;}
.tbl{width:100%; border-collapse:collapse; font-size:15px;}
.tbl th,.tbl td{border:1px solid #e8e8e8; padding:10px 12px; vertical-align:top;}
.tbl th{background:#f7f7f7; font-weight:800;}
.faq{border:1px solid #e6e6e6; border-radius:12px; padding:10px 12px; margin:10px 0; background:#fff;}
.faq summary{cursor:pointer; font-weight:800;}
.faq-a{margin-top:10px; line-height:1.75; color:#333;}
.cta{border-radius:16px; padding:16px 16px; background:#111; color:#fff; margin:26px 0;}
.cta strong{display:block; font-size:18px; margin-bottom:8px;}
</style>
""".strip()

    # 2) 대표 이미지(상단)
    top_img = f"""
<div style="margin-bottom:22px;">
  <img src="{hero_url}" alt="{escape(title)}"
       style="width:100%; border-radius:16px; box-shadow:0 6px 18px rgba(0,0,0,0.14);" />
</div>
""".strip()

    # 3) 요약 박스 + 목차
    summary_html = _box("info", "핵심 요약", _ul(summary) if summary else _p("핵심 포인트를 정리해 드립니다."))
    toc_links = []
    for s in sections:
        h = (s.get("h2") or "").strip()
        if not h:
            continue
        sid = _h2_id(h)
        toc_links.append(f"<li><a href='#{sid}'>{escape(h)}</a></li>")
    toc_html = f"""
<div class="toc">
  <div style="font-weight:900; margin-bottom:10px;">목차</div>
  <ol style="margin:0 0 0 1.2em;">{''.join(toc_links)}</ol>
</div>
""".strip()

    # 4) 섹션 렌더링 + 중간 이미지 1장(중간에 끼워넣기)
    body_parts = []
    mid_inserted = False
    mid_at = max(1, len(sections) // 2)

    for idx, s in enumerate(sections):
        h2 = (s.get("h2") or "").strip()
        paras = s.get("paras") or []
        bullets = s.get("bullets") or []
        tip = (s.get("tip") or "").strip()
        warn = (s.get("warn") or "").strip()

        if h2:
            sid = _h2_id(h2)
            body_parts.append(f"<h2 id='{sid}' style='margin:26px 0 10px; font-size:24px;'>{escape(h2)}</h2>")

        for p in paras:
            body_parts.append(_p(str(p)))

        if bullets:
            body_parts.append(_ul([str(x) for x in bullets]))

        if tip:
            body_parts.append(_box("tip", "실천 팁", _p(tip)))

        if warn:
            body_parts.append(_box("danger", "주의 신호", _p(warn)))

        # ✅ 중간 이미지 1회 삽입
        if (not mid_inserted) and idx == mid_at:
            body_parts.append(f"""
<div style="margin:24px 0;">
  <img src="{body_url}" alt="{escape(title)} 관련 이미지"
       style="width:100%; border-radius:16px; box-shadow:0 6px 16px rgba(0,0,0,0.12);" />
</div>
""".strip())
            mid_inserted = True

    # 5) 표
    table_html = ""
    if table.get("headers") and table.get("rows"):
        table_html = _box("warn", "한눈에 비교", _table(table["headers"], table["rows"]))

    # 6) FAQ
    faq_html = ""
    if faqs:
        faq_html = f"<h2 style='margin:26px 0 10px; font-size:24px;'>자주 묻는 질문</h2>\n{_faq(faqs)}"

    # 7) CTA
    cta_html = f"""
<div class="cta">
  <strong>마무리 한 줄</strong>
  <div style="line-height:1.75;">{escape(cta)}</div>
</div>
""".strip()

    return "\n".join([
        style,
        top_img,
        summary_html,
        toc_html,
        "<hr class='hr'/>",
        "\n".join(body_parts),
        table_html,
        faq_html,
        cta_html,
    ])
