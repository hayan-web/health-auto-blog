import os
import json
import requests
import google.generativeai as genai

# 1. 설정값 로드 (모든 가능한 이름을 다 검사합니다)
# 사용자가 설정했을 법한 모든 이름을 검색하여 하나라도 있으면 사용함
POSSIBLE_KEYS = ['GOOGLE_API_KEY', 'GEMINI_API_KEY', 'API_KEY', 'Gemini_API_Key']
RAW_KEY = None

for key_name in POSSIBLE_KEYS:
    val = os.getenv(key_name)
    if val:
        RAW_KEY = val
        print(f"✅ 시스템에서 '{key_name}'를 찾았습니다.")
        break

WP_URL = os.getenv('WP_URL', '').strip().rstrip('/')
WP_USER = os.getenv('WP_USERNAME', '').strip()
WP_PW = os.getenv('WP_APP_PASSWORD', '').replace(" ", "")

# 키가 전혀 없을 경우 상세 가이드 출력
if not RAW_KEY:
    print("❌ [비상] 깃허브 설정에서 API 키를 하나도 찾지 못했습니다!")
    print("💡 해결방법: GitHub Settings -> Secrets and variables -> Actions -> 'Repository secrets'에 GOOGLE_API_KEY를 만드세요.")
    exit(1)

# 2. Gemini 설정 (Gemini 3 Flash 기반 최신 엔진)
genai.configure(api_key=RAW_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

def generate_blog_content():
    # 4050 타겟 따뜻한 구어체 & 수채화풍 지침 (제공해주신 프롬프트 반영)
    system_instruction = """
    당신은 4050 건강 전문 블로그 작가입니다. JSON으로만 응답하세요.
    - 문체: 따뜻한 구어체 (~해요, ~네요), 마크다운 금지
    - 이미지: 파스텔톤 아날로그 수채화 일러스트 스타일 묘사
    - 구조: {"title": "제목", "content": "본문내용", "img_prompt": "이미지 영어 묘사"}
    """
    
    response = model.generate_content(
        system_instruction + "4050 세대에게 따뜻한 위로를 주는 건강 습관을 하나 골라 써주세요.",
        generation_config={"response_mime_type": "application/json"}
    )
    return json.loads(response.text)

def get_watercolor_image(img_prompt):
    # 수채화 스타일 강제 고정 (Imagen 3 풍)
    style = "soft analog watercolor illustration, pastel tones, calming and minimal"
    encoded_prompt = requests.utils.quote(f"{img_prompt}, {style}")
    return f"https://pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&model=imagen"

def publish_to_wp(data, img_url):
    # 가독성을 높인 HTML 본문 구성
    paragraphs = data['content'].split('\n')
    formatted_body = "".join([f"<p style='margin-bottom:1.5em; font-size:18px;'>{p.strip()}</p>" for p in paragraphs if p.strip()])
    
    final_html = f'''
    <div style="margin-bottom:30px;"><img src="{img_url}" style="width:100%; border-radius:15px; border:1px solid #eee;"></div>
    <div style="line-height:1.9; color:#333; font-family: 'Malgun Gothic', sans-serif;">
        {formatted_body}
    </div>
    '''
    
    auth = (WP_USER, WP_PW)
    payload = {"title": data['title'], "content": final_html, "status": "publish"}
    api_endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    
    try:
        res = requests.post(api_endpoint, auth=auth, json=payload, timeout=30)
        if res.status_code == 201:
            print(f"✅ 드디어 성공! 발행 주소: {res.json().get('link')}")
        else:
            print(f"❌ 워드프레스 거부 ({res.status_code}): {res.text}")
    except Exception as e:
        print(f"❌ 연결 오류: {e}")

if __name__ == "__main__":
    try:
        content_data = generate_blog_content()
        img_url = get_watercolor_image(content_data['img_prompt'])
        publish_to_wp(content_data, img_url)
    except Exception as e:
        print(f"❌ 중단: {e}")
