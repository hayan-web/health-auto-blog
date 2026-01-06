# app/html_sanitize.py
from __future__ import annotations

import re
from typing import Any, Dict, List


_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_HTML_PRE_CODE_RE = re.compile(r"<pre\b[^>]*>.*?</pre>", re.DOTALL | re.IGNORECASE)
_HTML_CODE_RE = re.compile(r"<code\b[^>]*>.*?</code>", re.DOTALL | re.IGNORECASE)

# “지시문/명령어” 느낌이 나는 줄(한국어/영문) 제거
_BAD_LINE_PATTERNS = [
    r"^\s*\[조건\].*$",
    r"^\s*\[주제\].*$",
    r"^\s*\[키워드\].*$",
    r"^\s*\[최근 제목\].*$",
    r"^\s*\[제목/구성 지시\].*$",
    r"^\s*조건을 지키며.*$",
    r"^\s*출력은.*만.*$",
    r"^\s*반드시.*하세요.*$",
    r"^\s*system\s*:\s*.*$",
    r"^\s*user\s*:\s*.*$",
    r"^\s*assistant\s*:\s*.*$",
    r"^\s*json\s*:\s*.*$",
    r"^\s*```.*$",
    r"^\s*import\s+\w+.*$",
    r"^\s*from\s+\w+.*$",
    r"^\s*def\s+\w+\(.*$",
    r"^\s*class\s+\w+.*$",
]

_BAD_LINE_RE = re.compile("|".join(_BAD_LINE_PATTERNS), re.IGNORECASE)


def _strip_bad_lines(text: str) -> str:
    if not text:
        return ""
    out_lines: List[str] = []
    for line in text.splitlines():
        if _BAD_LINE_RE.match(line.strip()):
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


def sanitize_html(html: str) -> str:
    """
    - 코드펜스/프리/코드 태그 제거
    - 인라인 코드(`...`) 제거(텍스트만 남김)
    - 지시문/명령어 라인 제거
    """
    if not html:
        return ""

    s = str(html)

    # 1) code fence 제거
    s = _CODE_FENCE_RE.sub("", s)

    # 2) <pre>, <code> 제거
    s = _HTML_PRE_CODE_RE.sub("", s)
    s = _HTML_CODE_RE.sub("", s)

    # 3) 인라인 코드: `텍스트` -> 텍스트
    s = _INLINE_CODE_RE.sub(r"\1", s)

    # 4) 지시문 라인 제거
    s = _strip_bad_lines(s)

    # 5) 너무 많은 공백 정리
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def sanitize_post_dict(post: Dict[str, Any]) -> Dict[str, Any]:
    """
    generate_blog_post 결과(dict)에서 sections/outro/summary 등 텍스트에 섞인 코드/지시문 제거
    """
    if not isinstance(post, dict):
        return post

    # title은 건드리지 않음(다른 곳에서 처리)
    for k in ["outro", "content", "img_prompt"]:
        if k in post and isinstance(post.get(k), str):
            post[k] = sanitize_html(post[k])

    # bullets
    for k in ["summary_bullets", "warning_bullets", "checklist_bullets"]:
        if k in post and isinstance(post.get(k), list):
            cleaned = []
            for x in post.get(k) or []:
                if isinstance(x, str):
                    t = sanitize_html(x)
                    if t:
                        cleaned.append(t)
            post[k] = cleaned

    # sections: 보통 [{title, bullets, body...}] 형태
    if isinstance(post.get("sections"), list):
        new_secs = []
        for sec in post["sections"]:
            if not isinstance(sec, dict):
                continue
            sec2 = dict(sec)
            for kk in ["title", "body", "text"]:
                if kk in sec2 and isinstance(sec2.get(kk), str):
                    sec2[kk] = sanitize_html(sec2[kk])
            if isinstance(sec2.get("bullets"), list):
                b2 = []
                for x in sec2.get("bullets") or []:
                    if isinstance(x, str):
                        t = sanitize_html(x)
                        if t:
                            b2.append(t)
                sec2["bullets"] = b2
            new_secs.append(sec2)
        post["sections"] = new_secs

    return post
