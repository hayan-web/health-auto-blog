import os
import re


# 중복 삽입 방지용 마커
_SLOT1_MARK = "<!--ADSENSE:SLOT1-->"
_SLOT2_MARK = "<!--ADSENSE:SLOT2-->"
_SLOT3_MARK = "<!--ADSENSE:SLOT3-->"


def _get_slot_code(slot_no: int) -> str:
    """
    슬롯별 광고 코드를 ENV에서 가져옵니다.
    - ADSENSE_SLOT1
    - ADSENSE_SLOT2
    - ADSENSE_SLOT3
    값이 없으면 빈 문자열 반환.
    """
    key = f"ADSENSE_SLOT{slot_no}"
    code = (os.getenv(key) or "").strip()
    return code


def _wrap(code: str, slot_no: int) -> str:
    """
    광고 코드 감싸기 (레이아웃 깨짐 방지)
    """
    if not code:
        return ""
    return (
        f"\n<div class='adsense-slot adsense-slot-{slot_no}' "
        f"style='margin:18px 0; padding:0; text-align:center;'>\n"
        f"{code}\n"
        f"</div>\n"
    )


def _already_injected(html: str, mark: str) -> bool:
    return mark in (html or "")


def _insert_before_first(html: str, patterns: list[str], insert_html: str) -> tuple[str, bool]:
    """
    patterns 중 하나라도 매칭되면, 그 매칭 시작 위치 앞에 insert_html을 삽입.
    """
    if not html or not insert_html:
        return html, False

    for pat in patterns:
        m = re.search(pat, html, flags=re.IGNORECASE | re.DOTALL)
        if m:
            idx = m.start()
            return html[:idx] + insert_html + html[idx:], True

    return html, False


def _insert_after_first(html: str, patterns: list[str], insert_html: str) -> tuple[str, bool]:
    """
    patterns 중 하나라도 매칭되면, 그 매칭 끝 위치 뒤에 insert_html을 삽입.
    """
    if not html or not insert_html:
        return html, False

    for pat in patterns:
        m = re.search(pat, html, flags=re.IGNORECASE | re.DOTALL)
        if m:
            idx = m.end()
            return html[:idx] + insert_html + html[idx:], True

    return html, False


def _append_to_end(html: str, insert_html: str) -> tuple[str, bool]:
    """
    맨 아래(가능하면 </div></body></html> 앞)로 삽입.
    """
    if not html or not insert_html:
        return html, False

    # </body> 앞
    m = re.search(r"</body\s*>", html, flags=re.IGNORECASE)
    if m:
        idx = m.start()
        return html[:idx] + insert_html + html[idx:], True

    # </html> 앞
    m = re.search(r"</html\s*>", html, flags=re.IGNORECASE)
    if m:
        idx = m.start()
        return html[:idx] + insert_html + html[idx:], True

    # 그냥 끝
    return html + insert_html, True


def inject_adsense_slots(html: str) -> str:
    """
    ✅ 수동 광고 3개 자동 삽입
    1) 요약박스 위에 1개
    2) 소제목 카드(첫 섹션) 위에 1개
    3) 글 맨 아래에 1개

    ENV에 ADSENSE_SLOT1~3가 없으면 해당 슬롯은 삽입되지 않습니다.
    이미 마커가 있으면 중복 삽입하지 않습니다.
    """
    if not html:
        return html

    slot1_code = _get_slot_code(1)
    slot2_code = _get_slot_code(2)
    slot3_code = _get_slot_code(3)

    # 하나도 없으면 그대로
    if not (slot1_code or slot2_code or slot3_code):
        return html

    out = html

    # -----------------------------
    # SLOT 1: 요약박스 위
    # -----------------------------
    if slot1_code and not _already_injected(out, _SLOT1_MARK):
        slot1 = _SLOT1_MARK + _wrap(slot1_code, 1)

        # 요약박스 후보 패턴들
        summary_patterns = [
            r"<div[^>]+class=[\"'][^\"']*(summary|summary-box|summarybox|key-summary|highlight-summary)[^\"']*[\"'][^>]*>",
            r"<section[^>]+class=[\"'][^\"']*(summary|summary-box|summarybox|key-summary|highlight-summary)[^\"']*[\"'][^>]*>",
            r"<!--\s*SUMMARY\s*-->",
        ]

        out, inserted = _insert_before_first(out, summary_patterns, slot1)

        # 그래도 못 찾으면: 첫 번째 H2(소제목) 위에라도 넣기
        if not inserted:
            out, inserted = _insert_before_first(
                out,
                [r"<h2[^>]*>", r"<h3[^>]*>"],
                slot1,
            )

        # 그래도 실패하면 문서 상단
        if not inserted:
            out = slot1 + out

    # -----------------------------
    # SLOT 2: 소제목 카드 위
    # -----------------------------
    if slot2_code and not _already_injected(out, _SLOT2_MARK):
        slot2 = _SLOT2_MARK + _wrap(slot2_code, 2)

        # 소제목 카드/섹션 카드 후보 패턴들
        section_patterns = [
            r"<div[^>]+class=[\"'][^\"']*(section-card|content-card|card|topic-card|post-card)[^\"']*[\"'][^>]*>",
            r"<section[^>]+class=[\"'][^\"']*(section-card|content-card|card|topic-card|post-card)[^\"']*[\"'][^>]*>",
            r"<h2[^>]*>",  # 마지막 fallback
        ]

        out, inserted = _insert_before_first(out, section_patterns, slot2)

        # 못 찾으면: summary 끝난 다음에 넣기
        if not inserted:
            out, inserted = _insert_after_first(
                out,
                [
                    r"</div>\s*<!--\s*SUMMARY\s*END\s*-->",
                    r"</section>\s*<!--\s*SUMMARY\s*END\s*-->",
                ],
                slot2,
            )

        # 그래도 실패하면: 상단 다음(대가성/첫 이미지 다음)쯤에 삽입
        if not inserted:
            out, inserted = _insert_after_first(
                out,
                [r"</div>\s*</div>", r"</div>\s*<div"],
                slot2,
            )

        if not inserted:
            out = out + slot2  # 최후: 아래로

    # -----------------------------
    # SLOT 3: 맨 아래
    # -----------------------------
    if slot3_code and not _already_injected(out, _SLOT3_MARK):
        slot3 = _SLOT3_MARK + _wrap(slot3_code, 3)
        out, _ = _append_to_end(out, slot3)

    return out
