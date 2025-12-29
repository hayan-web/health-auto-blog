import os
import json
import requests
import google.generativeai as genai

# 1. 설정값 로드 (여러 이름을 다 뒤져서 하나라도 걸리면 가져옵니다)
# 깃허브 Secrets에 GEMINI_API_KEY 또는 GOOGLE_API_KEY 중 하나만 있어도 됩니다.
RAW_KEY = os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY') or os.getenv('API_KEY')
WP_URL = os.getenv('WP_URL', '').strip().rstrip('/')
WP_USER = os.getenv('WP_USERNAME', '').strip()
WP_PW = os.getenv('WP_APP_PASSWORD', '').replace(" ", "")

# 키가 아예 없을 경우를 위한 상세 진단
if not RAW_KEY:
    print("❌ [비상] 깃허브 Secrets에서 키를 하나도 찾지 못했습니다!")
    print("조치사항: GitHub Settings -> Secrets -> Actions에 'GOOGLE_API_KEY'라는 이름으로 키를 등록했는지 다시 확인해주세요.")
    exit(1)

# 2. Gemini 설정
genai.configure(api_key=RAW_KEY)
model = genai.GenerativeModel('gemini-1.5-flash') 

def generate_blog_data():
    # 4050 세대 맞춤형 지침 (수채화풍, 구어체)
    system_instruction = """
    당신은 4050 건강 전문 작가입니다. JSON으로만 응답하세요.
    - 말투: '~해요', '~네요' 식의 따뜻한 구어체
    - 금지: 마크다운 기호(##, **), 특수문자 전면 금지
    - 이미지: 파스텔톤 아날로그 수채화 일러스트 스타일 묘사
    - 구조: {"title": "제목", "content": "본문내용", "img_prompt": "영어 묘사"}
    """
    
    response = model.generate_content(
        system_instruction + "4050 세대에게 따뜻한 위로와 건강 정보를 주는 글을 써주세요.",
        generation_config={"response_mime_type": "application/json"}
    )
    return json.loads(response.text)

def generate_watercolor_image(img_prompt):
    # 수채화 스타일 강제 고정
    style = "soft analog watercolor illustration, pastel tones, calming atmosphere"
    encoded_prompt = requests.utils.quote(f"{img_prompt}, {style}")
    return f"https://pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&model=imagen"

def publish_to_wp(data, img_url):
    # 줄바꿈 처리 및 폰트 크기 최적화
    paragraphs = data['content'].split('\n')
    formatted_body = "".join([f"<p style='margin-bottom:1.5em; font-size:18px; color:#333;'>{p.strip()}</p>" for p in paragraphs if p.strip()])
    
    final_html = f'''
    <div style="margin-bottom:30px;"><img src="{img_url}" style="width:100%; border-radius:15px;"></div>
    <div style="line-height:1.9;">{formatted_body}</div>
    '''
    
    auth = (WP_USER, WP_PW)
    payload = {"title": data['title'], "content": final_html, "status": "publish"}
    api_endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    
    try:
        res = requests.post(api_endpoint, auth=auth, json=payload, timeout=30)
        if res.status_code == 201:
            print(f"✅ 드디어 성공! 블로그 확인: {res.json().get('link')}")
        else:
            print(f"❌ 워드프레스 거부 ({res.status_code}): {res.text}")
    except Exception as e:
        print(f"❌ 연결 오류: {e}")

if __name__ == "__main__":
    try:
        content_data = generate_blog_data()
        image_url = generate_watercolor_image(content_data['img_prompt'])
        publish_to_wp(content_data, image_url)
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
