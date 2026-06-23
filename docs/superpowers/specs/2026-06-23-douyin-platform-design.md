# 抖音用户数据爬虫分析平台 — 设计文档

**Date:** 2026-06-23  
**Status:** Approved  
**Scope:** 个人单机工具，轻量级，单用户

---

## 1. 概述

### 1.1 目标

构建一个全栈平台，用于：
1. 爬取抖音指定用户主页的视频列表及互动数据（点赞、评论、转发、播放）
2. 在前端配置和控制爬虫（立即触发 + 定时自动）
3. 检索、筛选、展示历史爬取数据
4. 后续接入大模型做数据分析和对话式查询

### 1.2 约束

- 个人单机使用，Windows 10 部署
- 轻量级：SQLite，无外部依赖服务
- 单命令启动：`python main.py` 拉起全部（API + 静态文件托管）
- Phase 1 不包含 LLM 分析，但预留接口

---

## 2. 技术栈

| 层 | 技术 | 原因 |
|----|------|------|
| 爬虫引擎 | Scrapling + Playwright (已有) | 已验证可爬取抖音 |
| API | FastAPI (async) | 原生 async、WebSocket、Pydantic 集成 |
| 数据库 | SQLite + aiosqlite + SQLAlchemy | 零配置、文件即数据库 |
| 定时任务 | APScheduler | 进程内调度，与 FastAPI 共享生命周期 |
| 前端框架 | React 18 + Vite + TypeScript | 轻量 SPA |
| UI 组件 | Ant Design 5 | 表格/表单/日期选择器，数据面板首选 |
| 图表 | Recharts | 轻量 React 图表，趋势图够用 |
| 状态管理 | TanStack React Query | 服务端状态缓存 |
| 路由 | React Router v6 | SPA 标配 |

---

## 3. 数据模型

### 3.1 ER 图 (文字)

```
DouyinUser (1) ──→ (N) CrawlSession (1) ──→ (N) VideoSnapshot
ScheduleConfig (1) ←── (1) DouyinUser
```

### 3.2 表结构

**DouyinUser** — 被监控的抖音用户

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| sec_uid | VARCHAR(255) UNIQUE | 抖音用户唯一标识 |
| nickname | VARCHAR(255) | 昵称 |
| signature | TEXT | 签名 |
| avatar_url | VARCHAR(500) | 头像 |
| follower_count | INTEGER | 粉丝数 |
| created_at | DATETIME | 加入监控时间 |
| updated_at | DATETIME | 最后更新时间 |

**CrawlSession** — 每次爬取的快照

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| douyin_user_id | INTEGER FK | |
| trigger_type | VARCHAR(20) | `manual` / `scheduled` |
| status | VARCHAR(20) | `running` / `completed` / `failed` |
| started_at | DATETIME | |
| finished_at | DATETIME | |
| total_videos | INTEGER | 本次爬取到的视频数 |
| engagement_total | JSON | `{"likes":N, "comments":N, "shares":N, "plays":N}` |
| delta_from_prev | JSON | 较上次的变化 `{"new_videos":N, "likes_delta":N, ...}` |

**VideoSnapshot** — 单次会话中的视频数据

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| session_id | INTEGER FK | |
| aweme_id | VARCHAR(64) | 抖音视频 ID |
| desc | TEXT | 描述 |
| create_time | DATETIME | 视频发布时间 |
| duration_sec | FLOAT | 时长(秒) |
| cover_url | VARCHAR(500) | 封面图 |
| hashtags | JSON | `["话题1", "话题2"]` |
| music_title | VARCHAR(255) | BGM 名称 |
| likes | INTEGER | 点赞数 |
| comments | INTEGER | 评论数 |
| shares | INTEGER | 转发数 |
| plays | INTEGER | 播放数 |
| collects | INTEGER | 收藏数 |

**ScheduleConfig** — 定时爬取配置

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| douyin_user_id | INTEGER FK UNIQUE | |
| interval_minutes | INTEGER | 爬取间隔(分钟) |
| enabled | BOOLEAN | 是否启用 |
| last_run_at | DATETIME | |
| next_run_at | DATETIME | |

---

## 4. API 设计

Base path: `/api/v1`

### 4.1 用户管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/users` | 添加监控用户 `{sec_uid: str}` |
| GET | `/users` | 用户列表 `?page=1&page_size=20&search=xxx` |
| GET | `/users/{id}` | 用户详情 + 最新爬取统计 |
| DELETE | `/users/{id}` | 删除用户及关联数据 |
| POST | `/users/{id}/crawl` | 立即爬取，返回 `{session_id: int}` → 202 |

### 4.2 爬取会话

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/sessions` | 会话列表 `?user_id=&status=&date_from=&date_to=&page=&page_size=` |
| GET | `/sessions/{id}` | 会话详情 + 元信息 |
| GET | `/sessions/{id}/videos` | 该次视频 `?sort=likes&order=desc&page=&page_size=` |

### 4.3 视频检索

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/videos` | 跨会话检索 `?keyword=&user_id=&hashtag=&date_from=&date_to=&sort=likes&order=desc&page=&page_size=` |

### 4.4 定时任务

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/schedules` | 所有定时配置 |
| PUT | `/schedules/{user_id}` | 创建/更新配置 `{interval_minutes: int, enabled: bool}` |
| DELETE | `/schedules/{user_id}` | 删除定时 |

### 4.5 实时进度

| 方法 | 路径 | 说明 |
|------|------|------|
| WS | `/ws/crawl/{session_id}` | 推送 `{type: "progress", current:N, total:N, status:str}` |

### 4.6 LLM 分析 (Phase 2 预留)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/reports/{user_id}` | 报告列表 |
| POST | `/reports/{user_id}/generate` | 生成新报告 |
| POST | `/chat` | 对话式查询 |

---

## 5. 前端设计

### 5.1 路由

| 路由 | 页面 | 核心组件 |
|------|------|----------|
| `/` | Dashboard | StatCard ×4, RecentActivities, NextSchedules |
| `/users` | 用户管理 | UserTable, AddUserModal, CrawlButton, ScheduleConfig |
| `/users/:id` | 用户详情 | TrendChart, VideoTable, VideoDetailDrawer |
| `/crawler` | 爬虫控制 | RunningJobsPanel (WS 实时), HistoryLog |
| `/search` | 视频检索 | FilterBar, ResultTable, ExportButton |

### 5.2 组件树 (关键页面)

```
Dashboard
├── StatCards (用户数 / 总视频 / 今日爬取 / 下次定时)
├── RecentSessions (Table: 最近 10 次爬取)
└── UpcomingSchedules (List: 即将触发的定时)

UserDetail
├── UserHeader (头像、昵称、签名、粉丝数)
├── EngagementTrend (Recharts Line: 多次爬取的互动趋势)
├── VideoFilterBar (搜索框、排序下拉、日期范围)
└── VideoTable (虚拟滚动、展开行看详情)

CrawlerConsole
├── RunningJobs (Card × N, WS 实时进度条)
├── TriggerPanel (选择用户 → 立即爬取)
└── HistoryLog (Table: 最近失败/成功的任务)
```

---

## 6. 爬虫引擎设计

### 6.1 核心流程

```
POST /users/{id}/crawl
  → engine.crawler.crawl_user(sec_uid)
    → 启动 Playwright (系统 Chrome)
    → 加载用户主页
    → 拦截 /aweme/v1/web/aweme/post/ API 响应
    → 滚动页面加载更多
    → 每捕获一页立即写入 SQLite (async session)
    → 通过 WebSocket 推送进度
    → 完成后更新 CrawlSession status=completed
```

### 6.2 调度器流程

```
APScheduler (AsyncIO scheduler)
  → 每分钟检查 ScheduleConfig.next_run_at
  → 到期触发 engine.crawler.crawl_user()
  → 更新 last_run_at / next_run_at
```

### 6.3 爬虫复用

直接复用 `douyin_user_spider.py` 的核心代码（`crawl_douyin_user`、`extract_aweme`），将其封装为 async 函数，增加：
- 可注入 `db_session` 参数用于写入数据库
- 可注入 `progress_callback` 参数用于推送 WS 进度
- 错误处理和重试逻辑

---

## 7. 项目结构

```
FaPai_Spider/
├── backend/
│   ├── main.py              # FastAPI 入口，mount 静态文件
│   ├── config.py            # Settings (Pydantic BaseSettings)
│   ├── database.py          # SQLAlchemy engine + async session
│   ├── models.py            # ORM 模型 (4 tables)
│   ├── schemas.py           # Pydantic schemas
│   ├── api/
│   │   ├── __init__.py
│   │   ├── users.py
│   │   ├── sessions.py
│   │   ├── videos.py
│   │   ├── schedules.py
│   │   └── ws.py
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── crawler.py       # 爬虫核心 (复用 demo)
│   │   ├── scheduler.py     # APScheduler 管理
│   │   └── analyzer.py      # LLM 分析 (Phase 2)
│   └── static/              # 前端 build 产物
│
├── frontend/
│   ├── public/
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── api/             # axios 封装 + query keys
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── UserList.tsx
│   │   │   ├── UserDetail.tsx
│   │   │   ├── CrawlerConsole.tsx
│   │   │   └── VideoSearch.tsx
│   │   ├── components/
│   │   │   ├── StatCard.tsx
│   │   │   ├── VideoTable.tsx
│   │   │   ├── TrendChart.tsx
│   │   │   └── CrawlProgress.tsx
│   │   └── hooks/
│   │       ├── useCrawlProgress.ts  # WebSocket hook
│   │       └── useApi.ts           # React Query hooks
│   ├── package.json
│   ├── vite.config.ts
│   └── tsconfig.json
│
├── douyin_user_spider.py    # [保留] demo 参考
├── douyin_spider_framework.py  # [保留] Spider 框架参考
├── output/                  # 原始 JSON 输出 (已有)
├── requirements.txt
└── README.md
```

---

## 8. 启动方式

```bash
# 安装依赖
pip install fastapi uvicorn sqlalchemy aiosqlite apscheduler
pip install "scrapling[fetchers]"
cd frontend && npm install && npm run build

# 启动 (一个命令)
python backend/main.py
# → API: http://localhost:8000/api/v1
# → UI:  http://localhost:8000
```

---

## 9. Phase 2 预留

以下在 Phase 1 中考量但暂不实现：

- `engine/analyzer.py` — LLM 分析模块（接口已定义）
- `/chat` 端点 — 对话式分析
- `/reports/*` 端点 — 自动报告
- 前端 `ChatPanel` 组件 — 对话界面
- 视频下载功能

---

## 10. 设计决策记录

1. **SQLite over PostgreSQL** — 个人工具零运维，文件备份即备份数据库
2. **进程内调度 over Celery/Redis** — 不需要分布式，APScheduler 够用
3. **前端由 FastAPI 托管 over 独立部署** — 减少启动命令和端口
4. **快照模式 over 去重表** — 每次爬取保留完整快照，支持历史对比
5. **WebSocket over SSE** — 双向通信，后续可能需要前端发取消指令
6. **Ant Design over 手写 CSS** — 数据面板项目，组件库大幅提效
