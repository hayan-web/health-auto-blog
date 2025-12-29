import os
import json
import requests
import google.generativeai as genai

# 1. 설정값 로드
GEMINI_KEY = os.getenv('GEMINI_API_KEY')
WP_URL = os.getenv('WP_URL')
WP_USER = os.getenv('WP_USERNAME')
WP_PW = os.getenv('WP_APP_PASSWORD')

# 2. Gemini 설정
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash') 

def generate_blog_data():
    system_instruction = """
    당신은 4050 건강 전문 작가입니다. 반드시 JSON으로만 응답하세요.
    - 말투: 따뜻한 구어체 (~해요, ~네요)
    - 금지: 마크다운 기호(##, **), 특수문자, 표 구성
    - 이미지 프롬프트: 파스텔톤 수채화 스타일로 상세 묘사
    - JSON 구조: {"title": "제목", "content": "본문내용", "img_prompt": "이미지 영어 묘사"}
    """
    
    topic_prompt = "4050 세대에게 꼭 필요한 따뜻한 건강 습관 한 가지를 주제로 써주세요."
    
    # 안전하게 JSON을 받기 위한 설정
    response = model.generate_content(
        system_instruction + topic_prompt,
        generation_config={"response_mime_type": "application/json"}
    )
    return json.loads(response.text)

def generate_watercolor_image(img_prompt):
    style_tag = "soft analog watercolor illustration, pastel tones, calming and minimal"
    encoded_prompt = requests.utils.quote(f"{img_prompt}, {style_tag}")
    return f"https://pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&model=imagen"

def publish_to_wp(data, img_url):
    # 가독성을 높인 본문 구성
    paragraphs = data['content'].split('\n')
    formatted_body = "".join([f"<p style='margin-bottom:1.5em; font-size:18px;'>{p.strip()}</p>" for p in paragraphs if p.strip()])
    
    final_html = f'''
    <div style="margin-bottom:30px;">
        <img src="{img_url}" style="width:100%; border-radius:12px;">
    </div>
    <div style="line-height:1.8; color:#333;">
        {formatted_body}
    </div>
    '''
    
    auth = (WP_USER, WP_PW)
    payload = {
        "title": data['title'],
        "content": final_html,
        "status": "publish", # 이 부분이 반드시 'publish'여야 즉시 보입니다.
        "categories": [1]    # 기본 카테고리 ID (보통 1번)
    }
    
    # API 요청 주소 재확인 (끝에 /wp-json/wp/v2/posts 확인)
    api_endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"
    
    res = requests.post(api_endpoint, auth=auth, json=payload)
    
    if res.status_code == 201:
        print(f"✅ 성공적으로 발행되었습니다! 제목: {data['title']}")
    else:
        # 에러 발생 시 원인을 구체적으로 출력
        print(f"❌ 발행 실패: {res.status_code}")
        print(f"에러 내용: {res.text}")

if __name__ == "__main__":
    try:
        content_data = generate_blog_data()
        image_url = generate_watercolor_image(content_data['img_prompt'])
        publish_to_wp(content_data, image_url)
    except Exception as e:
        print(f"❌ 시스템 실행 중 오류: {e}")
