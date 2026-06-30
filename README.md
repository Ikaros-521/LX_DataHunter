# FaPai Spider

Multi-platform creator content monitor and crawler. 多渠道创作者内容爬取与监控系统。

## Features

- Multi-platform support: 抖音 (Douyin), B站 (Bilibili), 快手 (Kuaishou), 小红书 (Xiaohongshu)
- Creator management: add, list, delete monitored creators
- On-demand crawling with content deduplication
- Scheduled crawling with configurable intervals
- Content search with full-text, platform, date-range filters
- Engagement trend analytics
- Real-time crawl progress via WebSocket
- React SPA frontend with Ant Design

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+
- Chrome/Chromium (for headless browser crawling)

### Install

```bash
# Backend
pip install -r requirements.txt

# Frontend
cd frontend && npm install && npm run build
```

### Run

```bash
# 推荐用项目启动入口（Windows 下保证 Playwright 抓取可用）
python run.py

# 或直接用 uvicorn（注意：不要加 --reload，否则 Windows 下抓取会失败）
python -m uvicorn backend.main:app --host 0.0.0.0 --port 28000
```

Open http://localhost:28000 for the app, or http://localhost:28000/docs for the API docs.

> **Windows 注意**：不要用 `--reload` 启动后端。uvicorn 的热重载在 Windows 上会
> 强制使用不支持子进程的 `SelectorEventLoop`，导致抖音/B站的 Playwright 抓取报
> `NotImplementedError`。开发期改完代码手动重启 `python run.py` 即可。

### Development

```bash
# Backend：开发时如需热重载，可临时用 --reload，但抓取功能会不可用
python -m uvicorn backend.main:app --reload --port 28000

# Frontend dev server (port 3000, proxies API to :28000)
cd frontend && npm run dev
```

## Project Structure

```
backend/
  main.py          FastAPI app entry
  config.py        Settings (env vars)
  database.py      Async SQLAlchemy setup
  models.py        ORM models
  schemas.py       Pydantic schemas
  api/             REST API routers
  engine/          Crawler + scheduler
frontend/
  src/
    api/           React Query hooks + Axios client
    components/    Reusable UI components
    pages/         Route pages
    App.tsx        Router setup
```

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/v1/platforms` | List supported platforms |
| POST | `/api/v1/creators` | Add a creator |
| GET | `/api/v1/creators` | List creators |
| GET | `/api/v1/creators/{id}` | Get creator detail |
| DELETE | `/api/v1/creators/{id}` | Remove a creator |
| POST | `/api/v1/creators/{id}/crawl` | Trigger crawl |
| GET | `/api/v1/sessions` | List crawl sessions |
| GET | `/api/v1/contents` | Search contents |
| GET | `/api/v1/schedules` | List schedules |
| PUT | `/api/v1/schedules/{creator_id}` | Upsert schedule |
| WS | `/ws/crawl/{session_id}` | Crawl progress stream |
