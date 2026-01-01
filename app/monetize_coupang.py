import os
import re
from typing import Tuple


def _build_coupang_box(keyword: str) -> str:
    """
    쿠팡 파트너스 박스(간단 버전)
    - 실제 사용 중인 “딥링크/상품위젯” 방식에 맞게 여기만 바꾸시면 됩니다.
    - 아래는 예시: 쿠팡 검색 링크(파트너스 딥링크가 있다면 그걸로 교체 권장)
    """
    # ✅ 환경변수로 쿠팡 검색/딥링크 템플릿을 받도록 (선택)
    # 예: COUPANG_LINK_TEMPLATE="https://link.coupang.com/a/XXXXX?keyword={keyword}"
    tpl = (os.getenv("COUPANG_LINK_TEMPLATE") or "").strip()

    if tpl:
        url = tpl.format(keyword=keyword)
    else:
        # fallback: 파트너스 템플릿이 없으면 '삽입 불가'로 처리
        # (원하시면 여기 fallback을 일반 coupang 검색 URL로 바꿀 수는 있는데,
        #  파트너스 수익 목적이면 템플릿을 넣는 게 맞습니다.)
        url = ""

    if not url:
        return ""

    safe_kw = (keyword or "").strip()
    return f"""
<div class="coupang-box" style="margin:22px 0; padding:14px 14px; border:1px solid #e6e8ee; border-radius:14px; background:#fff;">
  <div style="display:flex; align-items:center; justify-content:space-between; gap:12px;">
    <div>
      <div style="font-size:15px; font-weight:700; color:#111; margin-bottom:4px;">추천 상품</div>
      <div style="font-size:13px; color:#666; line-height:1.5;">“{safe_kw}” 관련 상품을 확인해보세요.</div>
    </div>
    <a href="{url}" target="_blank" rel="nofollow sponsored noopener"
       style="flex:0 0 auto; display:inline-block; padding:10px 12px; border-radius:12px; background:#111; color:#fff; font-size:13px; text-decoration:none;">
      쿠팡에서 보기
    </a>
  </div>
</div>
""".strip()


def inject_coupang(html: str, keyword: str) -> Tuple[str, bool]:
    """
    (html, inserted) 반환
    - COUPANG_LINK_TEMPLATE가 없으면 삽입하지 않음 (False)
    - 이미 쿠팡 박스가 들어있으면 중복 삽입 안 함 (False)
    - 기본은 본문 '중간' 지점에 1회 삽입
    """
    html = html or ""
    keyword = (keyword or "").strip()

    if not keyword:
        return html, False

    # 이미 들어있으면 중복 삽입 방지
    if "coupang-box" in html or "link.coupang.com" in html:
        return html, False

    box = _build_coupang_box(keyword)
    if not box:
        return html, False

    # 문단 단위로 대략 중간에 삽입
    parts = re.split(r"(?i)(</p>)", html)
    # parts: [p_open...text..., </p>, ...] 형태로 섞임
    # 유효한 p 종료 태그 개수로 문단 수 추정
    p_end_count = sum(1 for x in parts if x.lower() == "</p>")
    if p_end_count <= 1:
        # 문단이 거의 없으면 그냥 끝에 삽입
        return (html + "\n" + box), True

    mid = max(1, p_end_count // 2)

    out = []
    seen_p_end = 0
    inserted = False

    for chunk in parts:
        out.append(chunk)
        if chunk.lower() == "</p>":
            seen_p_end += 1
            if (not inserted) and seen_p_end == mid:
                out.append("\n" + box + "\n")
                inserted = True

    final = "".join(out)
    return final, inserted
