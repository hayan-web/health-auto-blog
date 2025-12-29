import os
import requests
from openai import OpenAI

WP_URL = os.getenv('WP_URL')
WP_USERNAME = os.getenv('WP_USERNAME')
WP_PASSWORD = os.getenv('WP_APP_PASSWORD')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

client = OpenAI(api_key=OPENAI_API_KEY)

def generate_post():
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "당신은 4050 건강 전문 에디터입니다. 반드시 <h1> 태그 없이 <h2>, <h3> 만 사용하여 본문을 작성하세요. 코드 블록(```html)을 절대 사용하지 말고 순수 HTML 태그만 출력하세요."},
            {"role": "user", "content": "4050 세대에게 유용한 건강 주제로 블로그 글을 써줘. 제목은 첫 줄에 별도로 작성해줘."}
        ]
    )
    
    raw_content = response.choices[0].message.content
    # 불필요한 마크다운 코드 블록 제거
    clean_content = raw_content.replace('```html', '').replace('```', '').strip()
    
    lines = clean_content.split('\n')
    title = lines[0].replace('#', '').strip()
    content = '\n'.join(lines[1:])
    return title, content

def post_to_wordpress(title, content):
    endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    auth = (WP_USERNAME, WP_PASSWORD)
    data = {"title": title, "content": content, "status": "publish"}
    res = requests.post(endpoint, auth=auth, json=data)
    if res.status_code == 201:
        print("✅ 정제된 포스팅 성공!")

if __name__ == "__main__":
    title, content = generate_post()
    post_to_wordpress(title, content)
