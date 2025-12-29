import os
import requests
from openai import OpenAI

# 1. 설정값 불러오기
WP_URL = os.getenv('WP_URL')
WP_USERNAME = os.getenv('WP_USERNAME')
WP_PASSWORD = os.getenv('WP_APP_PASSWORD')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PEXELS_API_KEY = os.getenv('PEXELS_API_KEY')

client = OpenAI(api_key=OPENAI_API_KEY)

def generate_post():
    # AI에게 제목과 본문을 명확한 구분자로 달라고 요청합니다.
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "4050 건강 에디터. HTML로 작성. 코드블록(```) 절대 금지. 구조: [TITLE] 제목내용 [BODY] 본문내용 [KEYWORD] 영어검색어"},
            {"role": "user", "content": "4050 세대에게 유용한 건강 정보(예: 당뇨, 혈압, 관절)를 하나 골라 블로그 글을 써줘."}
        ]
    )
    res_text = response.choices[0].message.content
    
    # 데이터 분리 로직 (가장 안전한 방식)
    try:
        title = res_text.split('[TITLE]')[1].split('[BODY]')[0].strip()
        body = res_text.split('[BODY]')[1].split('[KEYWORD]')[0].strip()
        keyword = res_text.split('[KEYWORD]')[1].strip()
    except:
        # 분리 실패 시 예비책
        title = "4050을 위한 건강 관리 비법"
        body = res_text
        keyword = "health"

    return title, body, keyword

def get_pexels_image(query):
    # Pexels 사진 가져오기
    url = f"[https://api.pexels.com/v1/search?query=](https://api.pexels.com/v1/search?query=){query}&per_page=1"
    headers = {"Authorization": PEXELS_API_KEY}
    try:
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            data = res.json()
            if data['photos']:
                return data['photos'][0]['src']['large']
    except:
        pass
    # 사진 못 찾을 경우 사용할 고퀄리티 건강 기본 이미지
    return "[https://images.pexels.com/photos/4021775/pexels-photo-4021775.jpeg](https://images.pexels.com/photos/4021775/pexels-photo-4021775.jpeg)"

def post_to_wordpress(title, body, keyword):
    img_url = get_pexels_image(keyword)
    # 이미지 태그를 본문 맨 위에 강제 삽입
    img_html = f'<img src="{img_url}" alt="{title}" style="width:100%; max-height:500px; object-fit:cover; border-radius:10px;"><br><br>'
    
    final_content = img_html + body
    
    endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    auth = (WP_USERNAME, WP_PASSWORD)
    
    # 글 데이터 구성
    data = {
        "title": title,
        "content": final_content,
        "status": "publish",  # 즉시 발행
        "format": "standard"
    }
    
    res = requests.post(endpoint, auth=auth, json=data)
    if res.status_code == 201:
        print(f"✅ 발행 성공: {title}")
    else:
        print(f"❌ 실패 원인: {res.text}")

if __name__ == "__main__":
    title, body, keyword = generate_post()
    post_to_wordpress(title, body, keyword)
