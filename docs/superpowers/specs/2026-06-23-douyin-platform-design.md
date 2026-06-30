# 多平台创作者数据爬虫分析平台 — 设计文档

**Date:** 2026-06-23  
**Status:** Approved  
**Scope:** 个人单机工具，轻量级，单用户，多平台可扩展

---

## 1. 概述

### 1.1 目标

构建一个全栈平台，用于：
1. 爬取多平台创作者主页的内容列表及互动数据（点赞、评论、转发、播放）
2. 在前端配置和控制爬虫（立即触发 + 定时自动），支持按平台筛选
3. 检索、筛选、展示历史爬取数据，跨平台统一检索
4. 后续接入大模型做数据分析和对话式查询

### 1.2 平台支持计划

| 平台 | Phase 1 | 说明 |
|------|---------|------|
| 抖音 | ✅ 实现 | 已有可用爬虫 demo |
| B站 | Phase 2 | API 较开放，优先级高 |
| 快手 | Phase 2 | 与抖音类似，需浏览器 |
| 小红书 | Phase 3 | 反爬严格 |

架构上 Phase 1 就做好多平台扩展准备，新增平台只需添加一个适配器文件。

### 1.3 约束

- 个人单机使用，Windows 10 部署
- 轻量级：SQLite，无外部依赖服务
- 单命令启动：`python main.py` 拉起全部（API + 静态文件托管）
- Phase 1 实现抖音 + 多平台架构框架

---

## 2. 技术栈

| 层 | 技术 | 原因 |
|----|------|------|
| 爬虫引擎 | Scrapling + Playwright | 已有可用 demo，支持浏览器渲染 |
| API | FastAPI (async) | 原生 async、WebSocket、Pydantic 集成 |
| 数据库 | SQLite + aiosqlite + SQLAlchemy | 零配置、文件即数据库 |
| 定时任务 | APScheduler | 进程内调度 |
| 前端框架 | React 18 + Vite + TypeScript | 轻量 SPA |
| UI 组件 | Ant Design 5 | 表格/表单/日期选择器 |
| 图表 | Recharts | 轻量趋势图 |
| 状态管理 | TanStack React Query | 服务端状态缓存 |
| 路由 | React Router v6 | SPA 标配 |

---

## 3. 数据模型

### 3.1 ER 图

```
MonitoredCreator (1) ──→ (N) CrawlSession (1) ──→ (N) ContentSnapshot
ScheduleConfig    (1) ←── (1) MonitoredCreator
                                           ↑
                              platform 字段贯穿所有表
```

### 3.2 Platform 枚举

```python
class Platform(str, Enum):
    DOUYIN = "douyin"
    BILIBILI = "bilibili"
    KUAISHOU = "kuaishou"
    XIAOHONGSHU = "xiaohongshu"
```

### 3.3 表结构

**MonitoredCreator** — 被监控的创作者

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| platform | VARCHAR(20) | `douyin` / `bilibili` / ... |
| platform_uid | VARCHAR(255) | 平台内唯一标识（抖音 sec_uid, B站 mid） |
| nickname | VARCHAR(255) | 昵称 |
| signature | TEXT | 简介 |
| avatar_url | VARCHAR(500) | 头像 |
| follower_count | INTEGER | 粉丝数 |
| extra_info | JSON | 平台特有字段 `{"verified":true, "level":6, ...}` |
| created_at | DATETIME | |
| updated_at | DATETIME | |
| UNIQUE(platform, platform_uid) | | 同一平台同一用户唯一 |

**CrawlSession** — 每次爬取的快照

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| creator_id | INTEGER FK → MonitoredCreator | |
| platform | VARCHAR(20) | 冗余，方便查询 |
| trigger_type | VARCHAR(20) | `manual` / `scheduled` |
| status | VARCHAR(20) | `running` / `completed` / `failed` |
| error_message | TEXT | 失败原因 |
| started_at | DATETIME | |
| finished_at | DATETIME | |
| total_contents | INTEGER | 本次爬取到的内容数 |
| engagement_total | JSON | `{"likes":N, "comments":N, "shares":N, "plays":N}` |
| delta_from_prev | JSON | 较上次变化 `{"new_contents":N, "likes_delta":N, ...}` |

**ContentSnapshot** — 单次会话中的一条内容

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| session_id | INTEGER FK → CrawlSession | |
| content_id | VARCHAR(128) | 平台内内容 ID（抖音 aweme_id, B站 avid） |
| content_type | VARCHAR(20) | `video` / `image` / `article` (不同平台不同) |
| title | TEXT | 标题/描述 |
| cover_url | VARCHAR(500) | 封面图 |
| create_time | DATETIME | 内容发布时间 |
| duration_sec | FLOAT | 时长（视频），0 表示图文 |
| url | VARCHAR(500) | 内容链接 |
| tags | JSON | `["标签1", "标签2"]` |
| music_title | VARCHAR(255) | BGM（如有） |
| likes | INTEGER | 点赞 |
| comments | INTEGER | 评论 |
| shares | INTEGER | 转发/分享 |
| plays | INTEGER | 播放/阅读 |
| collects | INTEGER | 收藏 |
| extra_data | JSON | 平台特有字段 |

**ScheduleConfig** — 定时爬取

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | |
| creator_id | INTEGER FK UNIQUE | |
| interval_minutes | INTEGER | |
| enabled | BOOLEAN | |
| last_run_at | DATETIME | |
| next_run_at | DATETIME | |

---

## 4. API 设计

Base path: `/api/v1`

### 4.1 创作者管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/creators` | 添加创作者 `{platform, platform_uid}` |
| GET | `/creators` | 列表 `?platform=&search=&page=&page_size=` |
| GET | `/creators/{id}` | 详情 + 最新统计 |
| DELETE | `/creators/{id}` | 删除及关联数据 |
| POST | `/creators/{id}/crawl` | 立即爬取 → 202 `{session_id}` |

### 4.2 平台信息

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/platforms` | 返回支持的平台列表 `[{id, name, icon, status}]` |

### 4.3 爬取会话

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/sessions` | `?creator_id=&platform=&status=&date_from=&date_to=&page=&page_size=` |
| GET | `/sessions/{id}` | 会话详情 + 元信息 |
| GET | `/sessions/{id}/contents` | 该次内容列表 `?sort=likes&order=desc&page=&page_size=` |

### 4.4 内容检索

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/contents` | 跨创作者/平台检索 `?keyword=&creator_id=&platform=&tag=&content_type=&date_from=&date_to=&sort=likes&order=desc&page=&page_size=` |

### 4.5 定时任务

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/schedules` | 所有定时配置 |
| PUT | `/schedules/{creator_id}` | 创建/更新 `{interval_minutes, enabled}` |
| DELETE | `/schedules/{creator_id}` | 关闭定时 |

### 4.6 实时进度

| 方法 | 路径 | 说明 |
|------|------|------|
| WS | `/ws/crawl/{session_id}` | 推送 `{type:"progress", current:N, total:N, status:str}` |

### 4.7 LLM 分析 (Phase 2+)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/reports/{creator_id}` | 报告列表 |
| POST | `/reports/{creator_id}/generate` | 触发生成 |
| POST | `/chat` | 对话式查询 |

---

## 5. 爬虫引擎 — 策略模式

### 5.1 适配器接口

```python
class BasePlatformAdapter(ABC):
    platform: Platform

    @abstractmethod
    async def crawl_creator(
        self,
        platform_uid: str,
        max_items: int,
        progress_callback: Callable | None,
    ) -> List[ContentItem]:
        """爬取创作者内容列表"""
        ...

    async def resolve_creator_info(self, platform_uid: str) -> CreatorInfo:
        """解析创作者基本信息（昵称、头像、粉丝等）"""
        ...
```

### 5.2 适配器注册表

```python
# engine/platforms/__init__.py
ADAPTERS: dict[Platform, BasePlatformAdapter] = {
    Platform.DOUYIN: DouyinAdapter(),
    # Platform.BILIBILI: BilibiliAdapter(),   # Phase 2
}
```

### 5.3 引擎入口

```python
# engine/crawler.py
async def crawl_creator(
    creator: MonitoredCreator,
    db_session: AsyncSession,
    progress_cb: Callable,
) -> CrawlSession:
    adapter = ADAPTERS[creator.platform]
    # 1. 创建 CrawlSession (status=running)
    # 2. 调用 adapter.crawl_creator()
    # 3. 每条内容写入 ContentSnapshot
    # 4. 计算 delta，更新 session status=completed
```

### 5.4 Phase 1 实现

- `engine/platforms/douyin.py` — 封装现有 `douyin_user_spider.py` 核心逻辑
- 抽象接口和注册表已就位，后续平台只需添加一个文件

---

## 6. 前端设计

### 6.1 路由

| 路由 | 页面 | 核心组件 |
|------|------|----------|
| `/` | Dashboard | StatCard ×N, RecentSessions, UpcomingSchedules |
| `/creators` | 创作者管理 | CreatorTable (含 PlatformTag), AddCreatorModal, CrawlButton |
| `/creators/:id` | 创作者详情 | CreatorHeader, TrendChart, ContentTable, ContentDetailDrawer |
| `/crawler` | 爬虫控制 | RunningJobs (WS 进度), TriggerPanel, HistoryLog |
| `/search` | 内容检索 | PlatformFilter, FilterBar, ResultTable, ExportButton |

### 6.2 关键跨平台交互

- **添加创作者** — 先选平台 → 输入平台 UID → 系统自动解析昵称/头像
- **PlatformTag** — 全局通用组件，每个创作者/内容/会话旁显示彩色平台标签
- **Dashboard** — 统计卡片按平台分色展示
- **内容检索** — 顶部 PlatformFilter 多选，各平台内容统一表格展示

### 6.3 组件树（关键页面）

```
Dashboard
├── PlatformOverview (每平台一个 StatCard)
├── RecentSessions (Table: 平台标签 + 创作者 + 时间 + 状态)
└── UpcomingSchedules (List)

CreatorDetail
├── CreatorHeader (头像、昵称、签名、粉丝、PlatformTag)
├── EngagementTrend (Recharts: 多次爬取的互动趋势)
├── ContentFilterBar (搜索、排序、日期、类型)
└── ContentTable (展开行看详情)

CrawlerConsole
├── RunningJobs (Card × N, WS 实时进度条)
├── TriggerPanel (选创作者 → 立即爬取)
└── HistoryLog (Table)
```

---

## 7. 项目结构

```
FaPai_Spider/
├── backend/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── models.py
│   ├── schemas.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── creators.py
│   │   ├── platforms.py
│   │   ├── sessions.py
│   │   ├── contents.py
│   │   ├── schedules.py
│   │   └── ws.py
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── crawler.py          # 引擎入口 + 适配器路由
│   │   ├── scheduler.py        # APScheduler
│   │   ├── analyzer.py          # LLM (Phase 2+)
│   │   └── platforms/
│   │       ├── __init__.py      # 适配器注册表
│   │       ├── base.py          # BasePlatformAdapter 接口
│   │       └── douyin.py        # 抖音适配器 (Phase 1)
│   └── static/                  # 前端 build 产物
│
├── frontend/
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── api/
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── CreatorList.tsx
│   │   │   ├── CreatorDetail.tsx
│   │   │   ├── CrawlerConsole.tsx
│   │   │   └── ContentSearch.tsx
│   │   ├── components/
│   │   │   ├── PlatformTag.tsx    # 平台彩色标签
│   │   │   ├── StatCard.tsx
│   │   │   ├── ContentTable.tsx
│   │   │   ├── TrendChart.tsx
│   │   │   └── CrawlProgress.tsx
│   │   └── hooks/
│   │       ├── useCrawlProgress.ts
│   │       └── useApi.ts
│   ├── package.json
│   ├── vite.config.ts
│   └── tsconfig.json
│
├── douyin_user_spider.py       # [保留] demo 参考
├── douyin_spider_framework.py  # [保留] Spider 框架参考
├── output/                     # 原始 JSON 备份
├── requirements.txt
└── README.md
```

---

## 8. 启动方式

```bash
# 后端依赖
pip install fastapi uvicorn sqlalchemy aiosqlite apscheduler
pip install "scrapling[fetchers]"

# 前端构建
cd frontend && npm install && npm run build

# 启动
python backend/main.py
# → http://localhost:28000 (API + UI)
```

---

## 9. Phase 规划

| Phase | 内容 | 预估 |
|-------|------|------|
| **Phase 1** | FastAPI 骨架 + SQLite 模型 + 抖音适配器 + React 前端 5 页面 + 手动爬取 + 内容检索 | 本次 |
| **Phase 2** | 定时调度 + B站适配器 + LLM 自动报告 + 趋势对比图表增强 | 后续 |
| **Phase 3** | 快手/小红书适配器 + 对话式 LLM 分析 + 数据导出 | 后续 |

---

## 10. 设计决策记录

1. **策略模式 for 平台** — 每个平台一个适配器文件，新增平台不改核心代码
2. **`content_id` + `extra_data`** — 通用字段统一 schema，平台特有数据进 JSON
3. **`platform` 冗余到 session/content 表** — 避免跨表 JOIN 才能做平台筛选
4. **SQLite over PostgreSQL** — 个人工具零运维
5. **进程内调度 over Celery** — 不需要分布式
6. **前端由 FastAPI 托管** — 单命令启动
7. **快照模式 over 去重表** — 保留每次爬取的完整数据，支持历史对比
8. **Ant Design 组件库** — 数据面板项目，Table/Form/DatePicker 开箱即用
