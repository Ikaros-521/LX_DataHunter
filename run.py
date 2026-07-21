"""启动入口（Windows 推荐）。

为什么需要这个入口：
  uvicorn 在 Windows 下若以 --reload / --workers>1 启动，会强制使用
  WindowsSelectorEventLoopPolicy。该 loop 不支持子进程 (subprocess_exec)，
  会导致 Playwright 抓取（抖音/B站等）抛 NotImplementedError。

  本入口在导入 uvicorn 之前就把策略设为 ProactorEventLoop，并在「非 reload」
  模式下启动 —— 此时 uvicorn 不会覆盖策略，Playwright 可正常启动。

用法：
  python run.py                 # 默认 28000 端口，不开启 reload（推荐，抓取可用）
  python run.py --port 9000     # 指定端口
  python run.py --reload        # 开启热重载（注意：抓取会失败，仅开发调试用）

注意：
  若你已习惯直接用 `python -m uvicorn backend.main:app`（不带 --reload），
  那不会触发本问题，可继续那样用。
"""
import sys
import asyncio

# 必须在导入 uvicorn / 任何 asyncio loop 之前设置，对 Windows 生效
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def main():
    import argparse

    parser = argparse.ArgumentParser(description="启动 LX_DataHunter 后端")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=28000)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="开启热重载。注意：Windows 下 --reload 会使 Playwright 抓取失败，仅建议开发调试时使用。",
    )
    args = parser.parse_args()

    import uvicorn

    if args.reload and sys.platform == "win32":
        print(
            "[警告] --reload 模式下 uvicorn 会强制 SelectorEventLoop，"
            "Playwright 抓取会失败。如需抓取，请去掉 --reload。",
            file=sys.stderr,
        )

    uvicorn.run(
        "backend.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
