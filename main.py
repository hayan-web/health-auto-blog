import os
import json
import requests
import google.generativeai as genai

# 1. 설정값 로드 (공백 제거 로직 추가)
GEMINI_KEY = os.getenv('GEMINI_API_KEY')
WP_URL = os.getenv('WP_URL').strip().rstrip('/')
WP_USER = os.getenv('WP_USERNAME').strip()
WP_PW = os.getenv('WP_APP_PASSWORD').replace(" ", "") # 비밀번호 내 공백 자동 제거

genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash') 

def generate_blog_data():
    system_instruction = "4050 건강 전문 작가. JSON 응답: {'title': '제목', 'content': '본문', 'img_prompt': '이미지 묘사'}. 마크다운 금지."
    response = model.generate_content(system_instruction + "4050 건강 주제로 따뜻한 글 써줘.", 
                                      generation_config={"response_mime_type": "application/json"})
    return json.loads(response.text)

def publish_to_wp(data):
    # 이미지 생성 단계는 일단 제외하고 '글 발행' 성공부터 확인합니다.
    api_endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    auth = (WP_USER, WP_PW)
    
    payload = {
        "title": data['title'],
        "content": data['content'].replace('\n', '<br>'),
        "status": "publish"
    }
    
    print(f"📡 요청 주소: {api_endpoint}")
    print(f"👤 사용자: {WP_USER}")
    
    try:
        # 헤더에 User-Agent 추가 (일부 서버는 로봇의 접근을 차단함)
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.post(api_endpoint, auth=auth, json=payload, headers=headers, timeout=30)
        
        if res.status_code == 201:
            print(f"✅ 드디어 성공! 글 주소: {res.json().get('link')}")
        else:
            print(f"❌ 실패 코드: {res.status_code}")
            print(f"❌ 서버 답변: {res.text}") # 이 내용이 중요합니다.
            
    except Exception as e:
        print(f"❌ 연결 실패: {e}")

if __name__ == "__main__":
    content_data = generate_blog_data()
    publish_to_wp(content_data)
