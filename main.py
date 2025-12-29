import os
import json
import requests
import google.generativeai as genai

# 1. 설정값 로드 및 검증
# 깃허브 Secrets에 등록한 이름과 똑같아야 합니다.
GEMINI_KEY = os.getenv('GEMINI_API_KEY')
WP_URL = os.getenv('WP_URL', '').strip().rstrip('/')
WP_USER = os.getenv('WP_USERNAME', '').strip()
WP_PW = os.getenv('WP_APP_PASSWORD', '').replace(" ", "")

# 키가 없을 경우 상세 안내 출력
if not GEMINI_KEY:
    print("⚠️ 오류: GEMINI_API_KEY를 찾을 수 없습니다.")
    print("도움말: GitHub Settings -> Secrets -> Actions에 'GEMINI_API_KEY'라는 이름으로 키를 등록했는지 확인하세요.")
    exit(1)

# 2. Gemini 설정
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash') 

def generate_blog_data():
    # 사용자 지침 반영 (수채화풍, 따뜻한 구어체)
    system_instruction = """
    당신은 4050 건강 전문 작가입니다. JSON으로만 응답하세요.
    - 문체: 따뜻한 구어체 (~해요, ~네요), 마크다운 금지
    - 이미지 프롬프트: 파스텔톤 아날로그 수채화 일러스트 스타일
    - JSON 구조: {"title": "제목", "content": "본문내용", "img_prompt": "이미지 영어 묘사"}
    """
    
    response = model.generate_content(
        system_instruction + "4050 세대에게 따뜻한 위로를 주는 건강 습관을 하나 골라 써주세요.",
        generation_config={"response_mime_type": "application/json"}
    )
    return json.loads(response.text)

def generate_watercolor_image(img_prompt):
    style_tag = "soft analog watercolor illustration, pastel tones, calming"
    encoded_prompt = requests.utils.quote(f"{img_prompt}, {style_tag}")
    return f"https://pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&model=imagen"

def publish_to_wp(data, img_url):
    formatted_body = "".join([f"<p style='margin-bottom:1.5em; font-size:18px;'>{p.strip()}</p>" for p in data['content'].split('\n') if p.strip()])
    
    final_html = f'''
    <div style="margin-bottom:30px;"><img src="{img_url}" style="width:100%; border-radius:12px;"></div>
    <div style="line-height:1.8; color:#333;">{formatted_body}</div>
    '''
    
    auth = (WP_USER, WP_PW)
    payload = {
        "title": data['title'],
        "content": final_html,
        "status": "publish"
    }
    
    api_endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    
    try:
        res = requests.post(api_endpoint, auth=auth, json=payload, timeout=30)
        if res.status_code == 201:
            print(f"✅ 드디어 성공! 발행 주소: {res.json().get('link')}")
        else:
            print(f"❌ 서버 거부 (코드 {res.status_code}): {res.text}")
    except Exception as e:
        print(f"❌ 연결 오류: {e}")

if __name__ == "__main__":
    try:
        content_data = generate_blog_data()
        image_url = generate_watercolor_image(content_data['img_prompt'])
        publish_to_wp(content_data, image_url)
    except Exception as e:
        print(f"❌ 중단 사유: {e}")
