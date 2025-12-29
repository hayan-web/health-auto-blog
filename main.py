import os
import requests
from openai import OpenAI

# 설정값 불러오기
WP_URL = os.getenv('WP_URL')
WP_USERNAME = os.getenv('WP_USERNAME')
WP_PASSWORD = os.getenv('WP_APP_PASSWORD')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

client = OpenAI(api_key=OPENAI_API_KEY)

def generate_post_and_image():
    # 1. 건강 블로그 글 생성
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "4050 건강 전문 에디터입니다. 반드시 순수 HTML로만 작성하세요. 구조: [TITLE] 제목 [BODY] 본문 [IMAGE_PROMPT] 이미지 생성을 위한 영어 묘사(1문장)"},
            {"role": "user", "content": "4050 세대에게 유용한 건강 정보를 하나 선정해서 블로그 글을 써주세요."}
        ]
    )
    res_text = response.choices[0].message.content
    
    # 데이터 분할
    try:
        title = res_text.split('[TITLE]')[1].split('[BODY]')[0].strip()
        body = res_text.split('[BODY]')[1].split('[IMAGE_PROMPT]')[0].strip()
        img_prompt = res_text.split('[IMAGE_PROMPT]')[1].strip()
    except:
        title = "중년 건강 관리의 모든 것"
        body = res_text
        img_prompt = "A high-quality, realistic photo of healthy food and a middle-aged person exercising happily."

    # 2. DALL-E 3 이미지 생성 (대화창이 아닌 시스템 내부에서 실행)
    print(f"이미지 생성 중: {img_prompt}")
    img_res = client.images.generate(
        model="dall-e-3",
        prompt=f"A professional, realistic, and warm photo for a health blog. Topic: {img_prompt}. High resolution, 16:9 aspect ratio.",
        size="1024x1024",
        quality="standard",
        n=1,
    )
    img_url = img_res.data[0].url
    
    return title, body, img_url

def post_to_wordpress(title, body, img_url):
    # 이미지와 본문 결합
    img_html = f'<div style="margin-bottom:25px;"><img src="{img_url}" alt="{title}" style="width:100%; border-radius:15px;"></div>'
    final_content = img_html + body
    
    endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    auth = (WP_USERNAME, WP_PASSWORD)
    
    data = {
        "title": title,
        "content": final_content,
        "status": "publish"
    }
    
    res = requests.post(endpoint, auth=auth, json=data)
    if res.status_code == 201:
        print(f"✅ AI 이미지 포함 포스팅 완료: {title}")
    else:
        print(f"❌ 실패: {res.text}")

if __name__ == "__main__":
    title, body, img_url = generate_post_and_image()
    post_to_wordpress(title, body, img_url)
