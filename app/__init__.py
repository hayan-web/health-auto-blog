self.NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "").strip()
self.NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "").strip()
if not self.NAVER_CLIENT_ID or not self.NAVER_CLIENT_SECRET:
    print("⚠️ NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 이 비어있습니다. 키워드 선별이 동작하지 않을 수 있습니다.")
