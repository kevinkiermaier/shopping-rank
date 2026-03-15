# 쇼핑 키워드 분석툴

## 실행 방법

### 1. 네이버 API 키 발급
1. https://developers.naver.com 접속
2. 로그인 → Application 등록
3. 검색 API 선택
4. Client ID, Client Secret 복사

### 2. app.py에 API 키 입력
```python
NAVER_CLIENT_ID = "여기에_클라이언트_ID"
NAVER_CLIENT_SECRET = "여기에_클라이언트_시크릿"
```

### 3. 설치 & 실행
```bash
cd tools/shopping-rank
pip install -r requirements.txt
python app.py
```

### 4. 브라우저 접속
http://localhost:5050
