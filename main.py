import os
import requests
import json
from openai import OpenAI

# 설정값 불러오기
WP_URL = os.getenv('WP_URL')
WP_USERNAME = os.getenv('WP_USERNAME')
WP_PASSWORD = os.getenv('WP_APP_PASSWORD')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

client = OpenAI(api_key=OPENAI_API_KEY)

def generate_post_data():
    # 1. 사용자 프롬프트 반영 (JSON 응답 유도)
    system_prompt = """
    당신은 4050 건강 전문 실감형 블로그 작가입니다. 
    반드시 다음 지침을 준수하여 JSON 형식으로만 응답하세요.
    - 말투: 사실적인 구어체, 반말 금지, 자연스러운 흐름
    - 특수문자 및 마크다운(##, **, 불렛포인트) 절대 금지
    - 이미지: 실사 금지, 반드시 지브리 애니메이션 풍 또는 따뜻한 수채화풍 일러스트로 묘사
    - 형식: {"title": "제목", "content": "본문내용", "img_prompt": "DALL-E용 영어 프롬프트"}
    """
    
    response = client.chat.completions.create(
        model="gpt-4o",
        response_format={ "type": "json_object" }, # JSON 모드 활성화
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "40대와 50대가 가장 고민하는 '수면의 질 개선'에 대해 실제 상황처럼 생생하게 써줘."}
        ]
    )
    
    # JSON 파싱
    data = json.loads(response.choices[0].message.content)
    return data

def generate_ai_image(prompt):
    # 2. 애니메이션 풍 이미지 생성
    print(f"그림 그리는 중: {prompt}")
    img_res = client.images.generate(
        model="dall-e-3",
        prompt=f"{prompt}, Studio Ghibli style, warm lighting, cozy atmosphere, high quality digital art, 16:9 aspect ratio",
        size="1024x1024",
        n=1,
    )
    return img_res.data[0].url

def post_to_wordpress(data, img_url):
    # 본문 구성 (특수문자 없이 줄바꿈만 적용)
    content_html = f'<div style="margin-bottom:20px;"><img src="{img_url}" style="width:100%; border-radius:15px;"></div>'
    # 본문의 줄바꿈을 HTML 태그로 변환
    formatted_body = data['content'].replace('\n', '<br>')
    content_html += f'<div style="font-size:1.1rem; line-height:1.8;">{formatted_body}</div>'
    
    endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    auth = (WP_USERNAME, WP_PASSWORD)
    
    wp_data = {
        "title": data['title'],
        "content": content_html,
        "status": "publish"
    }
    
    res = requests.post(endpoint, auth=auth, json=wp_data)
    if res.status_code == 201:
        print(f"✅ 맞춤형 포스팅 완료: {data['title']}")

if __name__ == "__main__":
    post_json = generate_post_data()
    image_url = generate_ai_image(post_json['img_prompt'])
    post_to_wordpress(post_json, image_url)
