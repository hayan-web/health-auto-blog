import os
import json
import requests
import google.generativeai as genai

# 1. 설정값 로드 및 출력 (값이 있는지 없는지만 체크)
API_KEY = os.getenv('GOOGLE_API_KEY')
WP_URL = os.getenv('WP_URL', '').strip().rstrip('/')
WP_USER = os.getenv('WP_USERNAME', '').strip()
WP_PW = os.getenv('WP_APP_PASSWORD', '').replace(" ", "")

# 디버깅을 위해 설정값 존재 여부만 로그에 찍습니다 (실제 키는 유출 안 됨)
print(f"DEBUG: GOOGLE_API_KEY 존재 여부 = {bool(API_KEY)}")
print(f"DEBUG: WP_URL = {WP_URL}")
print(f"DEBUG: WP_USER = {WP_USER}")
print(f"DEBUG: WP_PW 존재 여부 = {bool(WP_PW)}")

if not API_KEY:
    print("❌ 오류: GOOGLE_API_KEY가 환경 변수로 전달되지 않았습니다.")
    exit(1)

# 2. Gemini 설정
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

def generate_blog():
    system_instruction = """
    당신은 4050 건강 전문 작가입니다. JSON으로만 응답하세요.
    - 말투: '~해요', '~네요' 따뜻한 구어체
    - 이미지: 파스텔톤 아날로그 수채화 일러스트 묘사
    - 구조: {"title": "제목", "content": "본문내용", "img_prompt": "이미지 영어 묘사"}
    """
    response = model.generate_content(
        system_instruction + "4050 세대에게 유용한 건강 습관을 하나 골라 써주세요.",
        generation_config={"response_mime_type": "application/json"}
    )
    return json.loads(response.text)

def get_image(prompt):
    style = "soft analog watercolor illustration, pastel tones, calming"
    encoded = requests.utils.quote(f"{prompt}, {style}")
    return f"https://pollinations.ai/p/{encoded}?width=1024&height=1024&model=imagen"

def publish(data, img_url):
    paragraphs = data['content'].split('\n')
    formatted_body = "".join([f"<p style='margin-bottom:1.5em; font-size:18px;'>{p.strip()}</p>" for p in paragraphs if p.strip()])
    
    final_html = f'''
    <div style="margin-bottom:30px;"><img src="{img_url}" style="width:100%; border-radius:15px;"></div>
    <div style="line-height:1.9; color:#333;">{formatted_body}</div>
    '''
    
    auth = (WP_USER, WP_PW)
    payload = {"title": data['title'], "content": final_html, "status": "publish"}
    api_endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    
    res = requests.post(api_endpoint, auth=auth, json=payload, timeout=30)
    if res.status_code == 201:
        print(f"✅ 드디어 성공! 발행 완료: {res.json().get('link')}")
    else:
        print(f"❌ 워드프레스 응답 오류 ({res.status_code}): {res.text}")

if __name__ == "__main__":
    try:
        data = generate_blog()
        url = get_image(data['img_prompt'])
        publish(data, url)
    except Exception as e:
        print(f"❌ 중단 사유: {e}")
