import os
import requests
from openai import OpenAI

# 1. 설정 (깃허브 비밀 금고에서 정보를 가져옵니다)
WP_URL = os.getenv('WP_URL')
WP_USERNAME = os.getenv('WP_USERNAME')
WP_PASSWORD = os.getenv('WP_APP_PASSWORD')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

client = OpenAI(api_key=OPENAI_API_KEY)

def generate_post():
    # 2. AI에게 글쓰기 요청 (4050 건강 주제)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "당신은 4050 세대를 위한 건강 전문 에디터입니다. 친절하고 전문적인 말투로 SEO에 최적화된 블로그 글을 작성하세요. 반드시 HTML 태그(h2, h3, p, ul, li)를 사용하여 구조화하세요."},
            {"role": "user", "content": "40대와 50대에게 꼭 필요한 건강 정보 중 하나를 선정해서 제목과 본문을 작성해줘. 본문에는 꼭 예방법과 추천 음식을 포함해줘."}
        ]
    )
    
    content = response.choices[0].message.content
    title = content.split('</h1>')[0].replace('<h1>', '').strip() if '<h1>' in content else "4050을 위한 건강 가이드"
    return title, content

def post_to_wordpress(title, content):
    # 3. 워드프레스에 글 올리기
    endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    auth = (WP_USERNAME, WP_PASSWORD)
    
    data = {
        "title": title,
        "content": content,
        "status": "publish" # 바로 발행 (검토 후 발행하려면 'draft'로 변경)
    }
    
    res = requests.post(endpoint, auth=auth, json=data)
    if res.status_code == 201:
        print("✅ 글 올리기 성공!")
    else:
        print(f"❌ 실패: {res.text}")

# 실행
if __name__ == "__main__":
    title, content = generate_post()
    post_to_wordpress(title, content)
