# LX_DataHunter

多渠道创作者内容爬取、下载与智能分析平台。支持抖音、B站等主流视频平台，提供创作者监控、定时抓取、视频下载到本地、ASR 语音转写及 LLM 多维分析的一站式工作流。

## 功能特性

| 模块 | 能力 |
|------|------|
| **多平台爬虫** | 抖音（Playwright 浏览器模拟）、B站（官方 API SDK），预留快手/小红书适配接口 |
| **创作者管理** | 添加/查看/删除关注创作者，自动更新粉丝数、头像、简介等元数据 |
| **按需 & 定时爬取** | 手动一键抓取 + APScheduler 可配置间隔的后台定时调度 |
| **热门内容搜索** | B站全站排行榜 / 热门视频；抖音关键词搜索（支持排序+时间筛选） |
| **去重存储** | 基于 `content_id` 自动去重，增量对比展示新增/消失内容与互动数据 delta |
| **视频下载** | yt-dlp 统一后端 + 抖音专用 Playwright 直链拦截（无水印），异步队列 + 并发控制 + 断点恢复 |
| **语音识别** | ffmpeg 抽音频 → faster-whisper 本地 ASR（也可切换阿里云在线 ASR），分段 + 全文输出 |
| **LLM 智能分析** | 三项可独立勾选：内容总结、脚本与制作手法分析、知识点提炼，并行调用 LLM API |
| **竞品调研** | 竞品账号管理（手动添加/关键词发现）→ 一键或定时调研：抓主页与近期视频、爬爆款视频评论区、页面截图 → LLM 内容策略/评论区洞察/市场对比分析 → 生成自包含 Markdown 报告（截图+视频链接+数据表），在线查看/下载 |
| **实时进度** | WebSocket 推送爬取、下载、分析、竞品调研全链路实时进度 |
| **凭证管理** | 集中管理各平台 Cookie，供爬虫和下载器携带登录态请求 |
| **React 前端** | Ant Design SPA，覆盖仪表盘、创作者列表/详情、爬虫控制台、内容搜索、热门抓取、下载管理、视频分析对比、竞品调研等页面 |

## 技术栈

| 层级 | 技术选型 |
|------|----------|
| 后端框架 | FastAPI + uvicorn + Pydantic v2 |
| 数据库 | SQLAlchemy 2.x (async) + SQLite (aiosqlite) |
| 任务调度 | APScheduler (AsyncIOScheduler) |
| 浏览器自动化 | Playwright (抖音爬虫/下载) |
| 视频下载 | yt-dlp (B站等平台)、自研直链拦截 (抖音) |
| 语音识别 | faster-whisper (本地) / 阿里云 ASR (在线) |
| 大模型 | OpenAI 兼容 API (DeepSeek 等) |
| 前端 | React 18 + TypeScript + Ant Design + React Query + WebSocket |

## 快速开始

### 前置条件

- Python 3.10+
- Node.js 18+
- Chrome / Chromium（爬虫与下载必需）
- ffmpeg（视频分析必需）
- （可选）NVIDIA GPU + CUDA（本地 ASR 加速）

### 安装

```bash
# 1. 后端依赖
pip install -r requirements.txt

# 2. 前端构建（生产模式）
cd frontend && npm install && npm run build
cd ..

# 3. 本地 ASR（可选，跳过则使用阿里云 ASR 或仅用 LLM 分析）
pip install faster-whisper
```

### 配置

复制环境变量模板并编辑：

```bash
cp .env.example .env
```

`.env` 配置项说明：

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `LLM_BASE_URL` | 空 | LLM API 端点（如 `https://api.deepseek.com/v1`） |
| `LLM_API_KEY` | 空 | LLM API 密钥 |
| `LLM_MODEL` | `deepseek-chat` | 调用的模型名称 |
| `LLM_TIMEOUT` | `120` | LLM 请求超时（秒） |
| `ASR_PROVIDER` | `local` | `local`（faster-whisper）或 `aliyun`（阿里云） |
| `WHISPER_MODEL` | `large-v3` | Whisper 模型大小（base/small/medium/large-v3） |
| `WHISPER_DEVICE` | `cuda` | 推理设备（`cuda` / `cpu`） |
| `max_concurrent_downloads` | `3` | 最大并发下载数 |
| `max_concurrent_analyses` | `2` | 最大并发分析数 |

> **注意**：若使用阿里云 ASR，需额外配置 `aliyun_asr_access_key`、`aliyun_asr_secret_key`、`aliyun_asr_app_key`。

### 启动

```bash
# 推荐方式（Windows 下保证 Playwright 可用）
python run.py

# 自定义端口
python run.py --port 9000

# 或直接用 uvicorn（不要加 --reload，否则 Windows 下抓取会失败）
python -m uvicorn backend.main:app --host 0.0.0.0 --port 28000
```

启动后访问：

- **应用**: http://localhost:28000
- **API 文档**: http://localhost:28000/docs

### 开发模式

```bash
# 后端（热重载，但抓取功能会不可用）
python -m uvicorn backend.main:app --reload --port 28000

# 前端（Vite 开发服务器，代理 API 到 :28000）
cd frontend && npm run dev
```

## Windows 注意事项

uvicorn 在 Windows 下若启用 `--reload` 或 `--workers > 1`，会强制使用 `SelectorEventLoopPolicy`，该事件循环不支持子进程 (`subprocess_exec`)，导致 Playwright 爬虫和 yt-dlp 下载抛出 `NotImplementedError`。

- **生产运行**：使用 `python run.py`（不传 `--reload`），入口脚本会在导入 uvicorn 前将策略设为 `ProactorEventLoop`。
- **开发调试**：可以临时用 `--reload`，但需接受抓取功能不可用，改完代码手动重启即可。

## 项目结构

```
LX_DataHunter/
├── backend/                   # 后端服务
│   ├── main.py               # FastAPI 入口（lifespan / 路由注册 / SPA 兜底）
│   ├── config.py             # Pydantic BaseSettings 配置（读 .env）
│   ├── database.py           # SQLAlchemy async engine & session
│   ├── models.py             # ORM 模型（7 张业务表）
│   ├── schemas.py            # Pydantic request/response schema
│   ├── api/                  # REST API routers
│   │   ├── creators.py       # 创作者 CRUD
│   │   ├── sessions.py       # 爬取会话列表
│   │   ├── contents.py       # 内容搜索（全文/平台/日期过滤）
│   │   ├── schedules.py      # 定时调度管理
│   │   ├── platforms.py      # 支持的平台列表
│   │   ├── credentials.py    # 平台凭证管理
│   │   ├── hot.py            # 热门内容爬取
│   │   ├── downloads.py      # 下载任务管理
│   │   ├── analysis.py       # 视频分析任务管理
│   │   ├── ws.py             # 爬取进度 WebSocket
│   │   ├── download_ws.py    # 下载进度 WebSocket
│   │   └── analysis_ws.py    # 分析进度 WebSocket
│   ├── engine/               # 核心引擎
│   │   ├── crawler.py        # 爬虫编排器（session 管理 / 去重 / delta）
│   │   ├── hot_crawler.py    # 热门/排行榜爬取（B站 API + 抖音搜索）
│   │   ├── downloader.py     # 异步下载队列（yt-dlp + 抖音直链拦截）
│   │   ├── analyzer.py       # 异步分析队列（ffmpeg → ASR → LLM）
│   │   ├── research_engine.py # 竞品调研编排（抓取→评论→截图→LLM→报告）
│   │   ├── comment_crawler.py # 评论区爬取（B站 SDK + 抖音 XHR 拦截）+ 截图
│   │   ├── competitor_discovery.py # 关键词发现竞品账号（B站搜索 + 抖音搜索）
│   │   ├── report_builder.py # 竞品调研 Markdown 报告拼装（纯函数）
│   │   ├── scheduler.py      # APScheduler 生命周期管理
│   │   ├── asr.py            # ASR provider 抽象（local / aliyun）
│   │   ├── llm_client.py     # LLM 客户端 + prompt 构造
│   │   └── platforms/        # 平台适配器
│   │       ├── base.py       # 抽象基类 + ContentItem/CreatorInfo
│   │       ├── douyin.py     # 抖音适配器（Playwright XHR 拦截 + Cookie 注入）
│   │       └── bilibili.py   # B站适配器
├── frontend/                 # React SPA
│   └── src/
│       ├── pages/            # 路由页面（Dashboard / Creator / Crawler / Analysis...）
│       ├── components/       # 可复用组件（ContentTable / TrendChart / Progress...）
│       ├── api/              # Axios 客户端 + React Query hooks + WS 封装
│       └── hooks/            # 进度监听 hooks（useCrawlProgress / useDownloadProgress...）
├── tests/                    # 后端测试
├── docs/                     # 文档
├── downloads/                # 下载文件输出目录
├── data.db                   # SQLite 数据库（自动创建）
├── run.py                    # 推荐启动入口（Windows EventLoop 修复）
├── requirements.txt          # Python 依赖
└── .env.example              # 环境变量模板
```

## 数据库设计

| 表名 | 用途 |
|------|------|
| `monitored_creators` | 关注的创作者（platform + platform_uid 唯一约束） |
| `crawl_sessions` | 每次爬取会话（含互动总量与增量 delta） |
| `content_snapshots` | 抓到的内容快照（视频/图文/文章的各项指标） |
| `schedule_configs` | 定时调度配置（interval_minutes + enabled） |
| `download_tasks` | 下载任务状态机（pending → downloading → completed/failed） |
| `video_analyses` | 视频分析结果（ASR 分段/全文 + 各维度 LLM 分析） |
| `platform_credentials` | 平台 Cookie 凭证（供爬虫和下载器携带） |
| `competitors` | 竞品账号（platform + platform_uid 唯一，调研后回填昵称/粉丝数） |
| `research_tasks` | 竞品调研任务（状态机 + 参数 + 各竞品统计 + 报告路径） |
| `research_comments` | 调研抓取的评论快照（按任务/竞品索引，支持跨报告对比） |
| `research_schedules` | 竞品调研定时配置（单行：interval + 参数覆盖） |

## API 端点

### 创作者管理

| Method | Path | 描述 |
|--------|------|------|
| GET | `/api/v1/platforms` | 获取支持的平台列表 |
| POST | `/api/v1/creators` | 添加创作者 |
| GET | `/api/v1/creators` | 列出所有创作者 |
| GET | `/api/v1/creators/{id}` | 创作者详情 |
| DELETE | `/api/v1/creators/{id}` | 删除创作者 |
| POST | `/api/v1/creators/{id}/crawl` | 触发手动爬取 |

### 爬取与内容

| Method | Path | 描述 |
|--------|------|------|
| GET | `/api/v1/sessions` | 列出爬取会话 |
| GET | `/api/v1/contents` | 搜索内容（支持全文/平台/日期范围过滤） |
| GET | `/api/v1/hot/categories` | 获取热门分类列表 |
| POST | `/api/v1/hot/crawl` | 触发热门内容爬取 |

### 下载与分析

| Method | Path | 描述 |
|--------|------|------|
| GET | `/api/v1/downloads` | 列出下载任务 |
| POST | `/api/v1/downloads` | 创建下载任务 |
| GET | `/api/v1/downloads/ytdlp-status` | 检测 yt-dlp 可用性 |
| GET | `/api/v1/analyses` | 列出分析任务 |
| POST | `/api/v1/analyses` | 创建分析任务 |
| POST | `/api/v1/analyses/batch` | 批量创建分析任务 |
| PUT | `/api/v1/analyses/{id}` | 更新分析任务设置（enabled_tasks / force_asr） |

### 凭证与调度

| Method | Path | 描述 |
|--------|------|------|
| GET/POST/PUT/DELETE | `/api/v1/credentials` | 平台凭证 CRUD |
| GET | `/api/v1/schedules` | 列出调度配置 |
| PUT | `/api/v1/schedules/{creator_id}` | 创建/更新调度 |

### 竞品调研

| Method | Path | 描述 |
|--------|------|------|
| GET/POST/PUT/DELETE | `/api/v1/competitors` | 竞品 CRUD（支持主页链接自动解析 uid） |
| POST | `/api/v1/competitors/discover` | 关键词发现候选竞品（B站纯 API；抖音需登录 Cookie） |
| POST | `/api/v1/research` | 创建调研任务（202，后台执行） |
| GET | `/api/v1/research` | 调研任务列表 |
| GET | `/api/v1/research/{id}` | 任务详情（状态/进度） |
| DELETE | `/api/v1/research/{id}` | 删除任务及报告文件 |
| GET | `/api/v1/research/{id}/file` | 报告 Markdown（`?download=true` 下载） |
| GET | `/api/v1/research/{id}/assets/{name}` | 报告截图资源 |
| GET/PUT | `/api/v1/research/schedule` | 定时调研配置 |
| WS | `/ws/research` | 调研进度广播 |

报告输出目录：`output/reports/{task_id}_{title}/report.md`（`assets/` 存截图，已 gitignore）。

### WebSocket

| 路径 | 描述 |
|------|------|
| `/ws/crawl/{session_id}` | 爬取实时进度 |
| `/ws/download` | 下载实时进度 |
| `/ws/analysis` | 分析实时进度 |

---

完整 API 文档（Swagger UI）: http://localhost:28000/docs

## 开发指南

### 添加新平台

1. 在 `backend/models.py` 的 `Platform` enum 中添加新平台
2. 在 `backend/engine/platforms/` 下新建适配器，继承 `BasePlatformAdapter`
3. 实现 `crawl_creator()` 方法，返回 `(CreatorInfo, list[ContentItem])`
4. 在 `backend/engine/platforms/__init__.py` 的 `get_adapter()` 中注册

### 测试

```bash
pytest tests/ -v
```

## License

内部项目
