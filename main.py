import os
import json
import requests
import google.generativeai as genai

# 1. 설정값 로드 및 검증
# 깃허브 시크릿 이름을 GEMINI_API_KEY 또는 GOOGLE_API_KEY 중 하나만 있어도 작동하게 보완
GEMINI_KEY = os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
WP_URL = os.getenv('WP_URL', '').strip().rstrip('/')
WP_USER = os.getenv('WP_USERNAME', '').strip()
WP_PW = os.getenv('WP_APP_PASSWORD', '').replace(" ", "")

if not GEMINI_KEY:
    raise ValueError("❌ 에러: API 키가 설정되지 않았습니다. GitHub Secrets를 확인하세요.")

# 2. Gemini 설정 (키 직접 전달 방식)
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash') 

def generate_blog_data():
    system_instruction = "4050 건강 전문 작가. JSON 응답: {'title': '제목', 'content': '본문', 'img_prompt': '이미지 묘사'}. 마크다운 및 특수문자 금지."
    # 안전한 JSON 생성을 위해 명시적으로 요청
    response = model.generate_content(
        system_instruction + "4050 건강 주제로 따뜻한 글 써줘.", 
        generation_config={"response_mime_type": "application/json"}
    )
    return json.loads(response.text)

def publish_to_wp(data):
    # 이미지 생성 단계는 발행 성공 확인 후 다시 합칠 예정입니다.
    api_endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    auth = (WP_USER, WP_PW)
    
    payload = {
        "title": data['title'],
        "content": data['content'].replace('\n', '<br>'),
        "status": "publish"
    }
    
    print(f"📡 발행 시도 주소: {api_endpoint}")
    
    try:
        res = requests.post(api_endpoint, auth=auth, json=payload, timeout=30)
        if res.status_code == 201:
            print(f"✅ 드디어 성공! 발행된 글 주소: {res.json().get('link')}")
        else:
            print(f"❌ 실패 코드: {res.status_code}")
            print(f"❌ 서버 응답: {res.text}")
    except Exception as e:
        print(f"❌ 연결 오류: {e}")

if __name__ == "__main__":
    try:
        content_data = generate_blog_data()
        publish_to_wp(content_data)
    except Exception as e:
        print(f"❌ 중단: {e}")
