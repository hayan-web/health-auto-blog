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
    # 사용자 제공 고도화 프롬프트 12가지 지침 반영
    system_instruction = """
    당신은 실제 사람이 운영하는 블로그의 주필입니다. 아래 지침을 엄격히 준수하여 JSON으로만 응답하세요.
    - 문체: 따뜻한 구어체 (~해요, ~네요), 사람이 말하듯 대화체 사용
    - 금지: 모든 마크다운 기호(##, **, 불렛포인트), 특수문자, 표 구성 절대 금지
    - 이미지 프롬프트 지침: 부드러운 파스텔톤 아날로그 수채화 일러스트 스타일. 의료 기기를 배제한 일상적 건강 관리 장면.
    - JSON 형식: {"title": "제목", "content": "본문내용", "img_prompt": "Imagen용 영어 묘사"}
    """
    
    topic_prompt = "4050 세대에게 따뜻한 위로와 정보를 주는 건강 생활 습관에 대해 써주세요."
    
    response = model.generate_content(
        system_instruction + topic_prompt,
        generation_config={"response_mime_type": "application/json"}
    )
    return json.loads(response.text)

def generate_watercolor_image(img_prompt):
    # Imagen 3 스타일을 구현하기 위한 수채화 특화 프롬프트 조합
    style_tag = "soft analog watercolor illustration, pastel tones, calming and minimal, high quality"
    encoded_prompt = requests.utils.quote(f"{img_prompt}, {style_tag}")
    return f"https://pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&model=imagen"

def publish_to_wp(data, img_url):
    # 특수문자 없는 순수 텍스트 기반 가독성 설계
    paragraphs = data['content'].split('\n')
    formatted_body = "".join([f"<p style='margin-bottom:1.6em; font-size:17px; color:#333;'>{p.strip()}</p>" for p in paragraphs if p.strip()])
    
    final_html = f'''
    <div style="margin-bottom:35px;">
        <img src="{img_url}" alt="{data['title']}" style="width:100%; border-radius:12px; border: 1px solid #eee;">
    </div>
    <div style="line-height:1.9; font-family: 'Nanum Gothic', sans-serif;">
        {formatted_body}
    </div>
    '''
    
    auth = (WP_USER, WP_PW)
    payload = {
        "title": data['title'],
        "content": final_html,
        "status": "publish"
    }
    
    res = requests.post(f"{WP_URL}/wp-json/wp/v2/posts", auth=auth, json=payload)
    if res.status_code == 201:
        print(f"✅ 수채화풍 감성 포스팅 성공: {data['title']}")

if __name__ == "__main__":
    try:
        content_data = generate_blog_data()
        image_url = generate_watercolor_image(content_data['img_prompt'])
        publish_to_wp(content_data, image_url)
    except Exception as e:
        print(f"시스템 오류 발생: {e}")
