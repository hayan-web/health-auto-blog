import os
import requests
from openai import OpenAI

# 설정 값 불러오기
WP_URL = os.getenv('WP_URL')
WP_USERNAME = os.getenv('WP_USERNAME')
WP_PASSWORD = os.getenv('WP_APP_PASSWORD')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PEXELS_API_KEY = os.getenv('PEXELS_API_KEY')

client = OpenAI(api_key=OPENAI_API_KEY)

def generate_post():
    # 1. AI 글 생성 (주제 선정 포함)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "4050 건강 전문 에디터. 순수 HTML(h2, h3)만 사용. 제목은 첫 줄에 작성."},
            {"role": "user", "content": "4050 세대에게 실질적인 도움이 되는 건강 정보(예: 관절, 혈압, 갱년기 등)를 하나 골라 블로그 글을 써줘."}
        ]
    )
    full_text = response.choices[0].message.content.replace('```html', '').replace('```', '').strip()
    lines = full_text.split('\n')
    title = lines[0].replace('#', '').strip()
    body = '\n'.join(lines[1:])
    return title, body

def get_pexels_image(query):
    # 2. Pexels API를 이용해 주제에 맞는 이미지 주소 가져오기
    url = f"https://api.pexels.com/v1/search?query={query}&per_page=1"
    headers = {"Authorization": PEXELS_API_KEY}
    try:
        res = requests.get(url, headers=headers)
        data = res.json()
        if data['photos']:
            # 내 서버 용량을 안 쓰기 위해 이미지 원본 링크만 가져옴
            return data['photos'][0]['src']['large']
    except:
        return None
    return None

def post_to_wordpress(title, body):
    # 3. 이미지 가져오기 (제목의 첫 단어로 검색)
    search_keyword = title.split()[0]
    img_url = get_pexels_image(search_keyword)
    
    img_html = ""
    if img_url:
        img_html = f'<img src="{img_url}" alt="{title}" style="width:100%; border-radius:10px;"><br><p style="color:gray; font-size:12px;">출처: Pexels</p><br>'
    
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
        print(f"✅ 포스팅 완료: {title}")

if __name__ == "__main__":
    title, body = generate_post()
    post_to_wordpress(title, body)
