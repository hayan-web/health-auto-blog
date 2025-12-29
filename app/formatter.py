def format_post_body(
    *,
    title: str,
    hero_url: str,
    body_url: str,
    intro: str = "",
    sections: list | None = None,
    outro: str = "",
    disclaimer: str = "",
) -> str:
    """
    - 상단 히어로 이미지
    - 요약 박스
    - 섹션 카드 스타일
    - 경고/주의 박스
    - 중간 이미지
    - 체크리스트 / FAQ 느낌
    """

    sections = sections or []

    def p(text: str) -> str:
        return f"<p style='font-size:17px; line-height:1.8; margin:0 0 14px; color:#222;'>{text}</p>"

    def section_box(title: str, body: str) -> str:
        return f"""
        <div style="
            background:#f7f9fb;
            border-left:5px solid #2f80ed;
            border-radius:10px;
            padding:18px 18px 16px;
            margin:28px 0;
        ">
            <h2 style="margin:0 0 10px; font-size:20px; color:#1a1a1a;">
                {title}
            </h2>
            {p(body)}
        </div>
        """

    def warning_box(body: str) -> str:
        return f"""
        <div style="
            background:#fff4f4;
            border:1px solid #ffb3b3;
            border-radius:10px;
            padding:16px;
            margin:26px 0;
        ">
            <strong style="color:#c62828;">⚠️ 주의</strong>
            {p(body)}
        </div>
        """

    def checklist(items: list[str]) -> str:
        lis = "".join(
            f"<li style='margin-bottom:8px;'>✅ {i}</li>" for i in items
        )
        return f"""
        <ul style="
            list-style:none;
            padding-left:0;
            margin:18px 0 24px;
            font-size:16px;
            line-height:1.7;
        ">
            {lis}
        </ul>
        """

    html = []

    # 🔝 상단 히어로 이미지
    html.append(f"""
    <div style="margin-bottom:28px;">
        <img src="{hero_url}" alt="{title}"
             style="width:100%; border-radius:16px; box-shadow:0 6px 18px rgba(0,0,0,0.15);" />
    </div>
    """)

    # 🧠 요약 박스
    if intro:
        html.append(f"""
        <div style="
            background:#eef5ff;
            border-radius:14px;
            padding:20px;
            margin-bottom:28px;
        ">
            <h2 style="margin:0 0 10px; font-size:20px;">📌 핵심 요약</h2>
            {p(intro)}
        </div>
        """)

    # 📚 본문 섹션들
    mid_inserted = False
    for idx, sec in enumerate(sections):
        sec_title = sec.get("title", "")
        sec_body = sec.get("content", "")

        # 중간 이미지 (딱 1번)
        if not mid_inserted and idx >= max(1, len(sections) // 2):
            html.append(f"""
            <div style="margin:34px 0;">
                <img src="{body_url}" alt="{title} 관련 이미지"
                     style="width:100%; border-radius:16px; box-shadow:0 6px 16px rgba(0,0,0,0.12);" />
            </div>
            """)
            mid_inserted = True

        html.append(section_box(sec_title, sec_body))

        # 경고/주의 섹션 자동 감지
        if any(k in sec_title for k in ["주의", "위험", "바로 병원", "경고"]):
            html.append(
                warning_box(
                    "통증이 갑작스럽게 심해지거나 호흡곤란, 어지럼증이 동반되면 즉시 의료기관을 방문하세요."
                )
            )

    # ✅ 체크리스트 느낌 마무리
    html.append(f"""
    <div style="
        background:#f1f8f5;
        border-radius:14px;
        padding:20px;
        margin:32px 0;
    ">
        <h2 style="margin:0 0 12px;">✔️ 이렇게 관리하세요</h2>
        {checklist([
            "통증 양상과 지속 시간을 기록하기",
            "무리한 활동은 피하고 충분한 휴식",
            "증상이 반복되면 전문의 상담"
        ])}
    </div>
    """)

    # 🔚 마무리 문단
    if outro:
        html.append(f"""
        <div style="margin-top:30px;">
            <h2 style="font-size:20px;">마무리 정리</h2>
            {p(outro)}
        </div>
        """)

    # ⚠️ 면책 문구
    if disclaimer:
        html.append(f"""
        <div style="
            font-size:14px;
            color:#666;
            margin-top:34px;
            padding-top:16px;
            border-top:1px solid #e0e0e0;
        ">
            {disclaimer}
        </div>
        """)

    return "\n".join(html)
