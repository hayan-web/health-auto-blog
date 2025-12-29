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
    # AI에게 제목과 본문, 그리고 '이미지 검색용 영어 키워드'를 따로 달라고 요청합니다.
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "4050 건강 에디터. HTML로 작성. 코드블록 금지. 마지막 줄에 'Keyword: 영어단어' 형식으로 이미지 검색어를 하나 적어줘."},
            {"role": "user", "content": "4050 세대에게 유용한 건강 정보를 하나 골라 블로그 글을 써줘."}
        ]
    )
    full_text = response.choices[0].message.content.replace('```html', '').replace('```', '').strip()
    
    # 텍스트에서 검색용 키워드만 추출
    if "Keyword:" in full_text:
        body_part = full_text.split("Keyword:")[0].strip()
        search_keyword = full_text.split("Keyword:")[1].strip()
    else:
        body_part = full_text
        search_keyword = "health" # 못 찾으면 기본값

    lines = body_part.split('\n')
    title = lines[0].replace('#', '').strip()
    content = '\n'.join(lines[1:])
    return title, content, search_keyword

def get_pexels_image(query):
    # Pexels API 호출
    url = f"https://api.pexels.com/v1/search?query={query}&per_page=1"
    headers = {"Authorization": PEXELS_API_KEY}
    try:
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            data = res.json()
            if data['photos']:
                return data['photos'][0]['src']['large']
    except Exception as e:
        print(f"이미지 검색 에러: {e}")
    return "https://images.pexels.com/photos/4021775/pexels-photo-4021775.jpeg" # 에러 시 보여줄 기본 건강 이미지

def post_to_wordpress(title, body, keyword):
    img_url = get_pexels_image(keyword)
    img_html = f'<img src="{img_url}" alt="{title}" style="width:100%; border-radius:10px;"><br><br>'
    
    final_content = img_html + body
    
    endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    auth = (WP_USERNAME, WP_PASSWORD)
    data = {"title": title, "content": final_content, "status": "publish"}
    
    res = requests.post(endpoint, auth=auth, json=data)
    if res.status_code == 201:
        print(f"✅ 이미지 포함 포스팅 성공: {title}")
    else:
        print(f"❌ 실패 원인: {res.text}")

if __name__ == "__main__":
    title, content, keyword = generate_post()
    post_to_wordpress(title, content, keyword)
