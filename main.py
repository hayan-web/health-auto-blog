import os
import json
import requests
import google.generativeai as genai

# 1. 설정값 로드 및 주소 자동 교정
GEMINI_KEY = os.getenv('GEMINI_API_KEY')
WP_URL = os.getenv('WP_URL').strip().rstrip('/') # 주소 끝의 슬래시 제거
WP_USER = os.getenv('WP_USERNAME')
WP_PW = os.getenv('WP_APP_PASSWORD')

# 2. Gemini 설정
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash') 

def generate_blog_data():
    system_instruction = """
    당신은 4050 건강 전문 작가입니다. 반드시 JSON으로만 응답하세요.
    - 문체: 따뜻한 구어체 (~해요, ~네요), 마크다운 금지
    - JSON 구조: {"title": "제목", "content": "본문내용", "img_prompt": "이미지 영어 묘사"}
    """
    
    topic_prompt = "4050 세대에게 따뜻한 위로를 주는 건강 정보를 하나 골라 써주세요."
    
    response = model.generate_content(
        system_instruction + topic_prompt,
        generation_config={"response_mime_type": "application/json"}
    )
    return json.loads(response.text)

def generate_watercolor_image(img_prompt):
    style_tag = "soft analog watercolor illustration, pastel tones"
    encoded_prompt = requests.utils.quote(f"{img_prompt}, {style_tag}")
    return f"https://pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&model=imagen"

def publish_to_wp(data, img_url):
    # 가독성을 위한 HTML 본문 구성
    formatted_body = "".join([f"<p style='margin-bottom:1.5em; font-size:18px;'>{p.strip()}</p>" for p in data['content'].split('\n') if p.strip()])
    
    final_html = f'''
    <div style="margin-bottom:30px;"><img src="{img_url}" style="width:100%; border-radius:12px;"></div>
    <div style="line-height:1.8;">{formatted_body}</div>
    '''
    
    auth = (WP_USER, WP_PW)
    payload = {
        "title": data['title'],
        "content": final_html,
        "status": "publish"
    }
    
    # API 주소를 더 확실하게 조립
    api_endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    print(f"📡 워드프레스 통신 시작: {api_endpoint}")
    
    try:
        res = requests.post(api_endpoint, auth=auth, json=payload, timeout=30)
        
        if res.status_code == 201:
            print(f"✅ [성공] 글이 발행되었습니다! 제목: {data['title']}")
        else:
            print(f"❌ [실패] 워드프레스 응답 코드: {res.status_code}")
            print(f"❌ [상세 에러 내용]: {res.text}") # 이 부분이 핵심입니다!
            
    except Exception as e:
        print(f"❌ [통신 오류] 서버와 연결할 수 없습니다: {e}")

if __name__ == "__main__":
    try:
        content_data = generate_blog_data()
        image_url = generate_watercolor_image(content_data['img_prompt'])
        publish_to_wp(content_data, image_url)
    except Exception as e:
        print(f"❌ 시스템 중단 사유: {e}")
