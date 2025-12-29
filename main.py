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

# 2. Gemini 설정 (사용자 요청: Gemini 3 Flash Preview급 성능 모델)
genai.configure(api_key=API_KEY)

# 최신 프리뷰 모델 명칭으로 시도 (안될 경우를 대비해 1.5 Pro를 예비로 둠)
try:
    model = genai.GenerativeModel('gemini-2.0-flash-thinking-exp')
except:
    model = genai.GenerativeModel('gemini-1.5-pro-latest')

def generate_blog():
    # 고도화된 4050 타겟 지침 반영
    system_instruction = """
    당신은 4050 건강 전문 작가입니다. 반드시 최종 결과를 JSON 형식으로 응답해주세요.
    - 말투: 따뜻한 구어체 (~해요, ~네요), 사람이 말하듯 대화체 사용
    - 금지: 모든 마크다운 기호(##, **), 특수문자 전면 금지
    - 이미지 묘사: NanoBanana 스타일의 독창적이고 예술적인 일러스트 묘사 포함
    - 구조: {"title": "제목", "content": "본문내용", "img_prompt": "NanoBanana style artistic illustration of..."}
    """
    
    # 12가지 지침을 반영한 글 생성
    response = model.generate_content(
        system_instruction + "4050 세대에게 따뜻한 위로와 실용적인 건강 정보를 주는 글을 써주세요.",
        generation_config={"response_mime_type": "application/json"}
    )
    return json.loads(response.text)

def get_nanobanana_image(prompt):
    # 이미지 모델을 'nanobanana'로 지정하여 특유의 스타일 유도
    style_tag = "NanoBanana style, vibrant yet calm, artistic watercolor touch"
    encoded_prompt = requests.utils.quote(f"{prompt}, {style_tag}")
    # pollinations API의 nanobanana 모델 적용
    return f"https://pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&model=nanobanana"

def publish_to_wp(data, img_url):
    # 줄바꿈 처리 및 4050 가독성 최적화
    paragraphs = data['content'].split('\n')
    formatted_body = "".join([f"<p style='margin-bottom:1.6em; font-size:18px; color:#333;'>{p.strip()}</p>" for p in paragraphs if p.strip()])
    
    final_html = f'''
    <div style="margin-bottom:30px;">
        <img src="{img_url}" alt="{data['title']}" style="width:100%; border-radius:15px; box-shadow: 0 4px 15px rgba(0,0,0,0.15);">
        <p style="text-align:right; font-size:13px; color:#888; margin-top:10px;">Artistic Touch by NanoBanana</p>
    </div>
    <div style="line-height:1.9; font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif;">
        {formatted_body}
    </div>
    '''
    
    auth = (WP_USER, WP_PW)
    payload = {
        "title": data['title'],
        "content": final_html,
        "status": "publish"
    }
    
    api_endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    res = requests.post(api_endpoint, auth=auth, json=payload, timeout=30)
    
    if res.status_code == 201:
        print(f"✅ [발행 성공] Gemini 2.0 & NanoBanana 완성! 주소: {res.json().get('link')}")
    else:
        print(f"❌ 워드프레스 거부 ({res.status_code}): {res.text}")

if __name__ == "__main__":
    try:
        content_data = generate_blog()
        image_url = get_nanobanana_image(content_data['img_prompt'])
        publish_to_wp(content_data, image_url)
    except Exception as e:
        print(f"❌ 시스템 중단: {e}")
