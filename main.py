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
    # 사용자 제공 고도화 프롬프트 반영
    system_instruction = """
    당신은 실제 사람이 운영하는 건강 블로그의 주필입니다. 아래 지침을 엄격히 준수하여 JSON으로만 응답하세요.
    
    [글쓰기 지침]
    - 말투: 따뜻한 구어체 (~해요, ~네요), 사람 냄새 나는 자연스러운 어조
    - 금지: 모든 마크다운 기호(##, **, 불렛포인트), 특수문자, 전문용어 나열, 표 구성 절대 금지
    - 내용: YMYL 주제의 신뢰성 유지, 건강 관리와 생활 습관 중심, 구체적 사례 포함
    - 이미지 프롬프트 지침: 부드러운 파스텔톤 아날로그 수채화 일러스트 스타일. 의료 기기/병원을 배제한 일상적 건강 관리 장면. 인물은 단순화된 얼굴로 묘사.
    
    JSON 형식: {"title": "제목", "content": "본문내용", "img_prompt": "Imagen용 영어 묘사"}
    """
    
    topic_prompt = "4050 세대가 공감할 수 있는 '건강한 식습관'이나 '생활 속 가벼운 운동' 중 하나를 골라 생생하게 써주세요."
    
    response = model.generate_content(
        system_instruction + topic_prompt,
        generation_config={"response_mime_type": "application/json"}
    )
    return json.loads(response.text)

def generate_watercolor_image(img_prompt):
    # Imagen 3 기반의 수채화풍 이미지 생성 (시스템 내부 호출용 주소 체계)
    print(f"이미지 생성 키워드: {img_prompt}")
    # 수채화 스타일을 강화하기 위한 접미사 추가
    style_suffix = "Soft pastel analog watercolor illustration, minimal detail, calming atmosphere, high quality digital art"
    encoded_prompt = requests.utils.quote(f"{img_prompt}, {style_suffix}")
    return f"https://pollinations.ai/p/{encoded_prompt}?width=1024&height=1024&model=imagen"

def publish_to_wp(data, img_url):
    # 특수문자 없이 줄바꿈만 처리하여 사람이 직접 쓴 듯한 느낌 강조
    paragraphs = data['content'].split('\n')
    formatted_body = "".join([f"<p style='margin-bottom:1.5em; font-size:17px;'>{p.strip()}</p>" for p in paragraphs if p.strip()])
    
    final_html = f'''
    <div style="margin-bottom:30px;">
        <img src="{img_url}" alt="{data['title']}" style="width:100%; border-radius:10px;">
        <p style="text-align:right; font-size:12px; color:#999; margin-top:5px;">따뜻한 일상의 기록</p>
    </div>
    <div style="line-height:1.8; color:#333; font-family: 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;">
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
        print(f"에러 발생: {e}")
