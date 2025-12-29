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
    # AI에게 명확하게 제목, 본문, 검색 키워드를 요청
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "4050 건강 전문 에디터입니다. 반드시 순수 HTML로만 작성하고 코드 블록(```)은 쓰지 마세요. 구조는 반드시 [TITLE] 제목 [BODY] 본문 [KEYWORD] 영어단어1개 형식으로만 출력하세요."},
            {"role": "user", "content": "4050 세대에게 유용한 건강 주제(예: 관절 건강, 혈압 관리 등)를 선정해서 블로그 글을 써주세요."}
        ]
    )
    res_text = response.choices[0].message.content
    
    # 데이터 분리
    try:
        title = res_text.split('[TITLE]')[1].split('[BODY]')[0].strip()
        body = res_text.split('[BODY]')[1].split('[KEYWORD]')[0].strip()
        keyword = res_text.split('[KEYWORD]')[1].strip()
    except:
        title = "중년 건강을 위한 필수 가이드"
        body = res_text
        keyword = "health"

    return title, body, keyword

def get_pexels_image(query):
    # Pexels API 호출 (키워드에 'health'를 조합하여 더 정확한 사진 유도)
    url = f"[https://api.pexels.com/v1/search?query=](https://api.pexels.com/v1/search?query=){query}+health&per_page=1"
    headers = {"Authorization": PEXELS_API_KEY}
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data['photos']:
                return data['photos'][0]['src']['large']
        else:
            print(f"Pexels API 에러: {res.status_code}")
    except Exception as e:
        print(f"이미지 검색 중 네트워크 에러: {e}")
    
    # [안전장치] 사진을 못 찾거나 에러가 나면 무조건 나오는 고퀄리티 기본 건강 이미지
    return "[https://images.pexels.com/photos/356040/pexels-photo-356040.jpeg](https://images.pexels.com/photos/356040/pexels-photo-356040.jpeg)"

def post_to_wordpress(title, body, keyword):
    img_url = get_pexels_image(keyword)
    
    # 이미지가 본문 맨 위에 크게 나오도록 설정 (내 서버 용량 0% 사용)
    img_html = f'''
    <div style="margin-bottom: 20px;">
        <img src="{img_url}" alt="{title}" style="width:100%; max-height:500px; object-fit:cover; border-radius:12px;">
        <p style="text-align:right; font-size:11px; color:#888;">사진 출처: Pexels</p>
    </div>
    '''
    
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
        print(f"✅ 포스팅 및 이미지 삽입 성공: {title}")
    else:
        print(f"❌ 실패 상세 원인: {res.text}")

if __name__ == "__main__":
    title, body, keyword = generate_post()
    post_to_wordpress(title, body, keyword)
