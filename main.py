import os
import json
import requests
import google.generativeai as genai

# 1. 설정값 로드
API_KEY = os.getenv('GOOGLE_API_KEY')
WP_URL = os.getenv('WP_URL', '').strip().rstrip('/')
WP_USER = os.getenv('WP_USERNAME', '').strip()
WP_PW = os.getenv('WP_APP_PASSWORD', '').replace(" ", "")

if not API_KEY:
    print("❌ 오류: GOOGLE_API_KEY를 찾을 수 없습니다.")
    exit(1)

# 2. Gemini 3 Flash Preview 설정
genai.configure(api_key=API_KEY)
# 최신 프리뷰 모델 명칭 적용
model = genai.GenerativeModel('gemini-2.0-flash-thinking-exp-1219') 

def generate_blog():
    # 고도화된 4050 지침 반영
    system_instruction = """
    당신은 4050 건강 전문 작가입니다. JSON으로만 응답하세요.
    - 말투: 따뜻한 구어체 (~해요, ~네요), 마크다운 금지
    - 이미지 묘사: NanoBanana 스타일의 독특하고 감각적인 일러스트 묘사
    - 구조: {"title": "제목", "content": "본문내용", "img_prompt": "NanoBanana style illustration of..."}
    """
    
    response = model.generate_content(
        system_instruction + "4050 세대에게 따뜻한 위로를 주는 건강 습관을 하나 골라 써주세요.",
        generation_config={"response_mime_type": "application/json"}
    )
    return json.loads(response.text)

def get_nanobanana_image(prompt):
    # NanoBanana 스타일을 이미지 생성 엔진에 반영
    style_tag = "NanoBanana style, vibrant yet soft colors, unique artistic touch, high quality"
    encoded = requests.utils.quote(f"{prompt}, {style_tag}")
    # 이미지 생성 모델 경로에 nanobanana 스타일 적용
    return f"https://pollinations.ai/p/{encoded}?width=1024&height=1024&model=nanobanana"

def publish(data, img_url):
    paragraphs = data['content'].split('\n')
    formatted_body = "".join([f"<p style='margin-bottom:1.6em; font-size:18px;'>{p.strip()}</p>" for p in paragraphs if p.strip()])
    
    final_html = f'''
    <div style="margin-bottom:30px;"><img src="{img_url}" style="width:100%; border-radius:15px; box-shadow: 0 4px 15px rgba(0,0,0,0.1);"></div>
    <div style="line-height:1.9; color:#333; font-family: 'Malgun Gothic', sans-serif;">
        {formatted_body}
    </div>
    <p style="text-align:right; color:#888; font-size:14px;">감성을 담은 NanoBanana 일러스트와 함께</p>
    '''
    
    auth = (WP_USER, WP_PW)
    payload = {"title": data['title'], "content": final_html, "status": "publish"}
    api_endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    
    res = requests.post(api_endpoint, auth=auth, json=payload, timeout=30)
    if res.status_code == 201:
        print(f"✅ [성공] Gemini 3 & NanoBanana 포스팅 완료: {res.json().get('link')}")
    else:
        print(f"❌ 발행 거부 ({res.status_code}): {res.text}")

if __name__ == "__main__":
    try:
        content_data = generate_blog()
        image_url = get_nanobanana_image(content_data['img_prompt'])
        publish(content_data, image_url)
    except Exception as e:
        print(f"❌ 시스템 오류: {e}")
