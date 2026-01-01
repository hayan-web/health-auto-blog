import os
import re
from typing import Tuple


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _build_disclosure_html() -> str:
    """
    쿠팡 링크가 실제로 들어갔을 때만 최상단에 넣을 문구
    (요청하신 대로 '최상단'에 크게/명확히)
    """
    disclosure_text = _env(
        "COUPANG_DISCLOSURE_TEXT",
        "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.",
    )
    return f"""
<div class="disclosure"
     style="margin:0 0 14px; padding:12px 14px; border-radius:10px;
            background:#fff3cd; border:1px solid #ffe69c;
            font-size:14px; line-height:1.6; color:#664d03;">
  <b>광고 안내</b><br/>
  {disclosure_text}
</div>
""".strip()


def _build_coupang_box(keyword: str) -> str:
    """
    쿠팡 박스 HTML
    - COUPANG_LINK_URL: 본인이 만든 쿠팡 파트너스 링크(필수)
    - COUPANG_BOX_TITLE: 박스 제목(선택)
    """
    link = _env("COUPANG_LINK_URL", "")
    if not link:
        return ""

    title = _env("COUPANG_BOX_TITLE", "추천 상품 확인하기")
    btn = _env("COUPANG_BOX_BUTTON", "쿠팡에서 관련 상품 보기")

    # 키워드가 있으면 링크 뒤에 subId 등 추적 파라미터를 붙이고 싶을 수 있으니 옵션 제공
    # (기본은 그대로 사용)
    add_kw = _env("COUPANG_APPEND_KEYWORD_PARAM", "0") == "1"
    if add_kw:
        sep = "&" if "?" in link else "?"
        link = f"{link}{sep}keyword={keyword}"

    return f"""
<div class="coupang-box"
     style="margin:18px 0; padding:16px; border-radius:14px;
            border:1px solid #e9ecef; background:#f8f9fa;">
  <div style="display:flex; justify-content:space-between; align-items:center; gap:12px;">
    <div>
      <div style="font-size:16px; font-weight:800; color:#212529; margin-bottom:6px;">
        {title}
      </div>
      <div style="font-size:14px; color:#495057; line-height:1.6;">
        ‘{keyword}’ 관련 상품을 한 번에 확인하실 수 있어요.
      </div>
    </div>
    <a href="{link}" target="_blank" rel="nofollow sponsored noopener"
       style="white-space:nowrap; text-decoration:none; font-weight:800;
              background:#198754; color:#fff; padding:10px 14px; border-radius:10px;">
      {btn}
    </a>
  </div>
</div>
""".strip()


def inject_coupang(html: str, keyword: str) -> Tuple[str, bool]:
    """
    ✅ 반환: (html, inserted_bool)

    동작:
    - COUPANG_LINK_URL 이 없으면: (원본 html, False)
    - 있으면:
      1) 최상단 wrap 바로 아래에 대가성 문구 삽입(최상단 규칙)
      2) 본문 중간(요약박스 아래/첫 섹션 위) 또는 대체 위치에 쿠팡 박스 1개 삽입
    """
    if not html:
        return html, False

    link = _env("COUPANG_LINK_URL", "")
    if not link:
        return html, False

    out = html
    inserted_any = False

    disclosure = _build_disclosure_html()
    coupang_box = _build_coupang_box(keyword)
    if not coupang_box:
        return html, False

    # -----------------------------------
    # 1) 최상단: wrap 바로 아래 disclosure
    # -----------------------------------
    if "<div class=\"wrap\">" in out:
        # 중복 방지
        if "class=\"disclosure\"" not in out:
            out = out.replace(
                "<div class=\"wrap\">",
                f"<div class=\"wrap\">\n{disclosure}\n",
                1,
            )
            inserted_any = True
    else:
        # wrap이 없다면 문서 최상단
        if "class=\"disclosure\"" not in out:
            out = disclosure + "\n" + out
            inserted_any = True

    # -----------------------------------
    # 2) 쿠팡 박스 삽입 위치
    #   우선순위:
    #   - summary 박스 끝난 직후
    #   - 첫 section-card 직전
    #   - 첫 h2 직전
    #   - 마지막에 append
    # -----------------------------------
    if "class=\"coupang-box\"" in out:
        # 이미 들어가 있으면 또 넣지 않음
        return out, True

    patterns_after_summary = [
        r"(</div>\s*<!--\s*SUMMARY\s*END\s*-->)",
        r"(</section>\s*<!--\s*SUMMARY\s*END\s*-->)",
        r"(</div>\s*</div>\s*<!--\s*SUMMARY\s*END\s*-->)",
    ]
    inserted = False
    for pat in patterns_after_summary:
        m = re.search(pat, out, flags=re.IGNORECASE | re.DOTALL)
        if m:
            idx = m.end()
            out = out[:idx] + "\n" + coupang_box + "\n" + out[idx:]
            inserted = True
            inserted_any = True
            break

    if not inserted:
        # 첫 카드(섹션) 앞
        m = re.search(
            r"<div[^>]+class=[\"'][^\"']*(section-card|content-card|card)[^\"']*[\"'][^>]*>",
            out,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if m:
            idx = m.start()
            out = out[:idx] + coupang_box + "\n" + out[idx:]
            inserted = True
            inserted_any = True

    if not inserted:
        # 첫 h2 앞
        m = re.search(r"<h2[^>]*>", out, flags=re.IGNORECASE | re.DOTALL)
        if m:
            idx = m.start()
            out = out[:idx] + coupang_box + "\n" + out[idx:]
            inserted = True
            inserted_any = True

    if not inserted:
        # 마지막
        out = out + "\n" + coupang_box
        inserted_any = True

    return out, inserted_any
