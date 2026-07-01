# 视频分析功能设计（v1：单视频分析）

- 日期：2026-07-01
- 状态：已批准（待实现）
- 关联：复用下载引擎（`backend/engine/downloader.py`）、下载文件接口（`GET /api/v1/downloads/{task_id}/file`）

## 1. 背景与目标

为已下载的视频追加"内容分析"能力：先用 ASR 拿到带时间轴的逐字稿，再用大模型分析脚本/拍摄/制作手法、总结内容，帮助用户学习爆款视频的运营手法、把自己的账号做好。

v1 聚焦**单视频分析**。"单用户多视频合并分析运营手法"为 v2（依赖 v1 先产出一批分析结果），本设计的路由与引擎结构已为其预留扩展位。

## 2. 关键决策（已与用户确认）

| 维度 | 决策 |
|------|------|
| 执行架构 | 照抄下载引擎：asyncio 队列 + worker + 状态机 + WebSocket 进度 |
| ASR | 两个 provider 都做、配置切换：本地 `faster-whisper` + 阿里云录音文件识别 |
| v1 默认 ASR | 本地 faster-whisper（用户机器有 NVIDIA GPU → large-v3 + cuda） |
| LLM | OpenAI 兼容接口（base_url + api_key + model），覆盖 DeepSeek/通义/GPT 等 |
| 凭证 | 一律走 `.env` / `FAPAI_` 环境变量，不硬编码 |
| v1 范围 | 仅单视频分析（ASR 时间轴+文本、脚本+拍摄+制作手法分析、内容总结） |

## 3. v1 范围

**包含**
- 对一个已完成下载的视频触发分析任务。
- ffmpeg 抽音频 → ASR（带时间轴）→ 两类 LLM 分析。
- 分析结果落库 + 前端展示（逐字稿时间轴、两类分析 markdown、可选视频联动）。

**不包含（v2）**
- 单用户多视频合并分析运营手法。
- per-analysis 独立重跑（v1 重跑为整条管线，但复用缓存 ASR）。

## 4. 数据模型

新增 `VideoAnalysis`（`backend/models.py`）。v1 与 `download_task` 1:1，重新分析就地更新并复用已缓存 ASR。

```
VideoAnalysis
  id                                  主键
  download_task_id  FK→download_tasks.id, unique, index
  content_snapshot_id, platform, content_id, title   # 冗余，便于列表展示
  status           pending|extracting|transcribing|analyzing|completed|failed
  stage            str   # 当前阶段中文文案，前端展示，如"语音识别中"
  progress         float # 0-100
  asr_provider     str   # local | aliyun（实际使用的）
  asr_segments     JSON  # [{start,end,text},...]  ASR 时间轴缓存
  transcript       Text  # 拼接纯文本，喂给 LLM
  duration_sec     float
  language         str   # 如 "zh"
  script_analysis  Text  # 脚本+拍摄+制作手法（markdown）
  content_summary  Text  # 内容总结（markdown）
  llm_model        str
  error_message    Text
  retry_count      int
  created_at, started_at, finished_at
```

要点：ASR 结果（`asr_segments`/`transcript`）与 LLM 结果（`script_analysis`/`content_summary`）分字段存储，故"只重跑 LLM"可复用缓存 ASR，不重做耗时的语音识别。

对应 `AnalysisStatus` 枚举 + Pydantic 响应 schema（`backend/schemas.py`）。

## 5. 分析流程（worker 内单任务管线）

```
EXTRACTING(0~10%)    ffmpeg 抽音频 → 16k 单声道 wav 临时文件
TRANSCRIBING(10~60%) asr_provider.transcribe(audio) → segments + transcript
                     （若 asr_segments 已存在且非 ?force_asr=true，跳过）
ANALYZING(60~99%)    2 个 LLM 调用并发：
                       ① 脚本结构 + 拍摄/制作手法分析
                       ② 内容总结
COMPLETED(100%)      落库
```

每阶段更新 `status/stage/progress` 并经 WebSocket 广播。临时音频文件在 finally 中清理。

## 6. AnalysisEngine（`backend/engine/analyzer.py`）

结构与 `download_engine` 同构：
- `asyncio.Queue[analysis_id]` + N 个 worker（`settings.max_concurrent_analyses`，默认 2）+ `asyncio.Semaphore`。
- `start()`：启动时把中断的 `analyzing` 还原为 `pending` 重新入队（同下载引擎）；`stop()`、`enqueue()`、`_worker()`、`_process_task()`。
- `set_broadcast(fn)` 接 WebSocket 广播函数；`_broadcast_progress(task)`。
- 全局单例 `analysis_engine`，在 `main.py` lifespan 中 `start()/stop()`。

## 7. ASR provider 抽象 + LLM client

```
AsrProvider(Protocol):
    name: str
    async transcribe(audio_path: str) -> AsrResult(segments: list[AsrSegment(start,end,text)], language: str)

FasterWhisperProvider   # faster-whisper(基于 ctranslate2，非 torch)；模型懒加载单例；
                        # transcribe 跑在 loop.run_in_executor 不阻塞事件循环；默认 large-v3 + cuda
AliyunAsrProvider       # 阿里云录音文件识别，httpx 直连 NLS REST
get_asr_provider()      # 按 settings.asr_provider 返回对应实现
```

```
LlmClient   # httpx 直连 {base_url}/chat/completions（OpenAI 兼容），无新重型依赖
  chat(system, user) -> markdown str
  内置两个 prompt 模板：脚本+拍摄+制作手法、内容总结
```

**已知实现细节（不阻塞 v1）**：阿里云"录音文件识别"通常要求音频位于可公网访问的 URL/OSS；v1 本地 provider 已可用，阿里云作为可切换备选，具体走"录音文件识别 + OSS/公网 URL"还是"支持本地上传的极速版"，在实现阶段确定。

## 8. 配置（`backend/config.py`，`FAPAI_` 前缀，走 `.env`）

```
FAPAI_ASR_PROVIDER=local              # local | aliyun
FAPAI_WHISPER_MODEL=large-v3
FAPAI_WHISPER_DEVICE=cuda
FAPAI_WHISPER_COMPUTE_TYPE=auto
FAPAI_ALIYUN_ASR_ACCESS_KEY=
FAPAI_ALIYUN_ASR_SECRET_KEY=
FAPAI_ALIYUN_ASR_APP_KEY=
FAPAI_LLM_BASE_URL=
FAPAI_LLM_API_KEY=
FAPAI_LLM_MODEL=deepseek-chat
FAPAI_LLM_TIMEOUT=120
FAPAI_MAX_CONCURRENT_ANALYSES=2
FAPAI_MAX_ANALYSIS_RETRIES=3
```

## 9. REST API（`backend/api/analysis.py`，照抄 downloads.py 风格）

```
POST   /api/v1/analysis             {download_task_id}  建任务并入队
                                      （校验该下载已完成且有文件；1:1 已存在则就地重跑）
GET    /api/v1/analysis             列表（分页，按 status/platform 过滤）
GET    /api/v1/analysis/{id}        详情（含 segments/transcript/两类分析）
POST   /api/v1/analysis/{id}/retry  重跑（默认复用缓存 ASR；?force_asr=true 强制重做 ASR）
DELETE /api/v1/analysis/{id}        删记录（不动视频文件）
GET    /api/v1/analysis/check       检测 ffmpeg/ASR provider/LLM 配置是否就绪（仿 check-ytdlp）
```

## 10. WebSocket（`backend/api/analysis_ws.py`，照抄 download_ws.py）

`/ws/analysis`，广播 `{id, status, stage, progress, error_message, ...}`；前端钩子 `useAnalysisProgress`（照抄 `useDownloadProgress`）。

## 11. 前端

- 导航新增"**视频分析**"；路由 `/analysis`（列表）+ `/analysis/:id`（详情）。子路由结构预留 v2 的 `/analysis/creator/:id`。
- **列表页 `VideoAnalysis.tsx`**：分析任务表（状态/进度/阶段文案/平台）；"**新建分析**"按钮 → 弹窗列出"已下载完成但未分析"的视频，选中即建任务并跳转详情。
- **详情页 `VideoAnalysisDetail.tsx`**（Tabs）：
  - `内容总结`、`脚本与制作手法`：markdown 渲染（`react-markdown`）；
  - `逐字稿`：时间轴分段列表 `[mm:ss] 文本`；内嵌 `<video src=/api/v1/downloads/{id}/file>`，点击分段 `video.currentTime = segment.start` 跳转（复用已实现的下载文件接口）；
  - 头部：标题/平台/状态进度条 + 阶段文案 + 重试/删除。

## 12. 错误处理

- ffmpeg / 选定 ASR provider / LLM 配置任一缺失 → `/analysis/check` 反映；任务置 `failed` + 可读 `error_message`。
- LLM 超时/限流/鉴权失败、抽音频失败、ASR 返回空 → 分别捕获、给出可读错误；沿用下载引擎的重试机制（`max_analysis_retries`）。
- 临时音频文件始终在 `finally` 清理。

## 13. 测试

- 纯函数单测（不碰真实 GPU/网络，全 mock）：ASR 分段解析、`transcript` 拼接、LLM prompt 构造、结果字段映射、`get_asr_provider` 选择、阶段/进度计算。
- 真实 ASR/LLM 由用户本地验证（与下载功能一致的验证方式）。

## 14. 依赖与安装

- 后端：`faster-whisper`（带 ctranslate2）加入 `requirements.txt`；ffmpeg 为系统二进制（启动检测 `shutil.which("ffmpeg")`）；GPU 需 CUDA（ctranslate2 GPU 支持，一次性配置说明）；阿里云用 httpx 直连，不引重型 SDK。
- 前端：新增 `react-markdown`。

## 15. 受影响文件（实现时）

沿用现有目录约定（`backend/engine/` + `backend/api/`，不新增 `services/`）：

- 新增：`backend/engine/analyzer.py`（引擎）、`backend/engine/asr.py`（provider 抽象 + FasterWhisper/Aliyun 实现 + `get_asr_provider`）、`backend/engine/llm_client.py`、`backend/api/analysis.py`、`backend/api/analysis_ws.py`、`frontend/src/pages/VideoAnalysis.tsx`、`frontend/src/pages/VideoAnalysisDetail.tsx`、`frontend/src/hooks/useAnalysisProgress.ts`。
- 修改：`backend/models.py`、`backend/schemas.py`、`backend/config.py`、`backend/main.py`（lifespan + 路由 + WS）、`frontend/src/App.tsx`（路由）、`frontend/src/components/AppLayout.tsx`（导航）、`requirements.txt`、`frontend/package.json`。

## 更新记录

- 2026-07-01：初版，v1 单视频分析设计，经用户逐节确认。
