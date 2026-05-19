# Frontend Dashboard

Next.js dashboard MVP for the FastAPI backend.

## Setup

Install dependencies:

```powershell
cd frontend
npm install
```

Create a local environment file:

```powershell
Copy-Item .env.example .env.local
```

Expected backend URL:

```text
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

## Run

Start the FastAPI backend first:

```powershell
cd ..\backend
uvicorn app.main:app --reload
```

Start the frontend:

```powershell
cd ..\frontend
npm run dev
```

Open:

```text
http://localhost:3000/dashboard
```

## Pages

- `/dashboard`
- `/jobs`
- `/jobs/[id]`
- `/saved`
- `/missing-skills`
- `/role-fit`
- `/salary`
- `/sources`
