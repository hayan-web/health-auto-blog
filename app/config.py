    def __init__(self):
        # API Keys
        self.OPENAI_API_KEY = _get_env("OPENAI_API_KEY", required=True)
        self.GOOGLE_API_KEY = _get_env("GOOGLE_API_KEY", required=True)

        # WordPress
        self.WP_URL = _get_env("WP_URL", required=True).rstrip("/")
        self.WP_USERNAME = _get_env("WP_USERNAME", required=True)
        self.WP_APP_PASSWORD = _get_env("WP_APP_PASSWORD", required=True).replace(" ", "")

        # Models
        self.OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
        self.GEMINI_IMAGE_MODEL = os.getenv(
            "GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"
        ).strip() or "gemini-2.5-flash-image"

        # Naver API
        self.NAVER_CLIENT_ID = _get_env("NAVER_CLIENT_ID", required=True)
        self.NAVER_CLIENT_SECRET = _get_env("NAVER_CLIENT_SECRET", required=True)
