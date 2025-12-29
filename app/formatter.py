# app/formatter.py
from __future__ import annotations
import re
from typing import List, Dict, Any


def _p(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    return f'<p style="margin:0 0 14px; font-size:17px; line-height:1.75; letter-spacing:-0.2px;">{text}</p>'


def _h2(text: str) -> str:
    text = (text or "").strip()
    return f'<h2 style="margin:26px 0 12px; font-size:22px; line-height:1.35;">{text}</h2>'


def _h3(text: str) -> str:
    text = (text or "").strip()
    return f'<h3 style="margin:20px 0 10px; font-size:19px; line-height:1.4;">{text}</h3>'


def _hr() -> str:
    return '<hr style="border:none;border-top:1px solid #e9e9e9;margin:24px 0;">'


def callout_box(title: str, bullets: List[str]) -> str:
    items = "".join([f"<li style='margin:6px 0'>{b}</li>" for b in bullets if (b or "").strip()])
    return f"""
    <div style="border:1px solid #e9e9e9;border-radius:14px;padding:16px 16px 12px;background:#fafafa;margin:18px 0;">
      <div style="font-weight:700;margin-bottom:10px;">{title}</div>
      <ul style="margin:0;padding-left:18px;line-height:1.6;">{items}</ul>
    </div>
    """.strip()


def toc_box(headings: List[str]) -> str:
    items = "".join([f"<li style='margin:6px 0'>{h}</li>" for h in headings if (h or "").strip()])
    return f"""
    <div style="border:1px solid #e9e9e9;border-radius:14px;padding:14px 16px;background:#ffffff;margin:18px 0;">
      <div style="font-weight:700;margin-bottom:8px;">목차</div>
      <ol style="margin:0;padding-left:18px;line-height:1.6;">{items}</ol>
    </div>
    """.strip()


def format_post_body(
    title: str,
    intro: str,
    sections: List[Dict[str, Any]],
    outro: str,
    disclaimer: str = "",
) -> str:
    """
    sections: [{ "h2": "...", "paras": ["...", "..."], "h3": [{"t":"", "paras":[...]}] }]
    """
    # 목차 추출
    h2s = [s.get("h2", "") for s in sections if s.get("h2")]
    html = []
    html.append(_p(intro))
    if h2s:
        html.append(toc_box(h2s))
    html.append(_hr())

    for s in sections:
        if s.get("h2"):
            html.append(_h2(s["h2"]))

        for para in (s.get("paras") or []):
            html.append(_p(para))

        for sub in (s.get("h3") or []):
            if sub.get("t"):
                html.append(_h3(sub["t"]))
            for para in (sub.get("paras") or []):
                html.append(_p(para))

        html.append(_hr())

    html.append(_p(outro))

    if disclaimer:
        html.append(f"""
        <div style="font-size:13px; color:#666; background:#f7f7f7; border-radius:12px; padding:12px 14px; margin-top:18px; line-height:1.6;">
          {disclaimer}
        </div>
        """.strip())

    return "\n".join([x for x in html if x])
