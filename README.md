# Portfolio Manage

포트폴리오 관리 웹 애플리케이션 (백엔드 API + DB + 프론트엔드)

## 상태
- 1단계 데이터 관리 진행 중. 전체 로드맵은 [docs/plans/00-overview.md](docs/plans/00-overview.md) 참고.
- 화면별 기능, 자동 배치(cron) 작업, 재설정 방법은 [docs/MANUAL.md](docs/MANUAL.md) 참고.

## 기술 스택
- 백엔드: Python + FastAPI
- 데이터베이스: PostgreSQL
- 프론트엔드: React + Vite + TypeScript
- 개인용 로컬 단일 사용자 (인증 없음)

## 빠른 시작

### 1. DB
```bash
docker compose up -d
```

### 2. 백엔드
```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 필요 시 값 수정
alembic upgrade head
uvicorn app.main:app --reload --port 8001
```
API 문서: http://localhost:8001/docs

### 3. 프론트엔드
```bash
cd frontend
npm install
cp .env.example .env   # 필요 시 값 수정
npm run dev
```
http://localhost:5173

> 참고: 이 개발 환경에서는 포트 8000이 다른 프로세스가 이미 사용 중이라 백엔드를 8001로 띄운다. 다른 환경이라면 `.env`의 `VITE_API_BASE_URL`과 `CORS_ORIGINS`를 원하는 포트에 맞게 조정하면 된다.
