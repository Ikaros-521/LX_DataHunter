# LX_DataHunter 功能使用说明（图解版）

> 多平台视频数据采集与分析系统 · 开源地址：<https://github.com/Ikaros-521/LX_DataHunter>
>
> 本文通过功能页面截图介绍系统各模块的使用方式。配套介绍视频见 [LX_DataHunter_intro.mp4](./LX_DataHunter_intro.mp4)。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量（LLM/ASR 相关可选，不配不影响采集功能）
cp .env.example .env

# 3. 启动后端（默认 28000 端口，同时托管前端页面）
python run.py

# 4. 浏览器访问
http://localhost:28000
```

前端开发模式：`cd frontend && npm install && npm run dev`（端口 3000，API 代理到 28000）。

## 系统总览

侧边栏按「数据工作流」分为四组：**数据采集 → 数据中心 → 分析洞察 → 系统设置**，首屏为全局概览。

## 概览（数据看板）

监控创作者数、总爬取次数、今日爬取、活跃定时任务，以及最近爬取活动和即将执行的定时任务。

![概览](screenshots/01_dashboard.png)

## 数据采集

### 创作者管理

添加抖音 / B站创作者（支持主页 URL 或平台 UID），平台筛选、立即爬取、查看详情、删除。

![创作者管理](screenshots/02_creators.png)

### 创作者详情

展示平台 UID、简介、加入时间等档案信息，播放 / 点赞 / 评论 / 转发的**互动趋势图**，以及逐次爬取历史（可单独清除）。

![创作者详情](screenshots/03_creator_detail.png)

### 爬虫控制台

选择创作者 + 条数后「立即爬取」；下方列出全部 168 条爬取记录（手动 / 定时触发、状态、内容数），支持按平台、状态、创作者、关键词检索与批量清理。

![爬虫控制台](screenshots/04_crawler.png)

### 热门爬取

无需指定创作者，直接抓取平台热门 / 排行榜内容（如 B站热门、每周必看、全站排行榜），结果入库后即可检索分析。

![热门爬取](screenshots/05_hot.png)

### 定时任务

为创作者配置爬取间隔（如每 5 分钟 / 30 分钟），系统每分钟检查一次到期任务自动执行，间隔从上次执行完成起算。

![定时任务](screenshots/06_schedules.png)

## 数据中心

### 内容检索

多维度筛选：关键词、平台、创作者、类型、点赞数区间、发布日期区间，支持导出。列表展示封面、标题、点赞 / 评论 / 分享 / 播放数据。

![内容检索](screenshots/07_search.png)

### 下载管理

基于 yt-dlp 的视频下载：总任务 / 下载中 / 已完成 / 失败统计，实时进度、失败重试、预览、另存为。

![下载管理](screenshots/08_downloads.png)

## 分析洞察

### 视频分析

对库内视频发起分析：ASR 语音转写（本地 / API）+ LLM 生成内容总结与知识提炼，支持批量分析。

![视频分析](screenshots/09_analysis.png)

### 分析详情

LLM 输出一句话概括、关键要点、目标人群与价值主张，附完整逐字稿；支持多选视频跨对比与跨视频大总结。

![分析详情](screenshots/13_analysis_detail.png)

### 竞品调研

两种模式：**账号分析**（指定竞品账号）与**赛道分析**（关键词搜索 Top 视频）。自动爬取视频评论区与弹幕，可配置 LLM 筛选相关视频、评论全爬、定时自动调研。

![竞品调研](screenshots/10_research.png)

### 调研报告

生成结构化报告：市场概览（LLM 跨赛道对比）、赛道总览（平均播放 / 点赞 / 评论）、赛道详情，支持在线查看、下载 Markdown、弹幕原始数据浏览与 CSV 导出。

![调研报告](screenshots/12_research_report.png)

## 系统设置

### 凭证管理

配置抖音等平台的登录 Cookie，用于需要登录态的采集场景。

![凭证管理](screenshots/11_credentials.png)

## 开源说明

本项目已开源：<https://github.com/Ikaros-521/LX_DataHunter>

功能目前为初步实现，尚未做深度优化——能满足基本使用，如有需要请自行优化，欢迎 Star / Fork / PR。
