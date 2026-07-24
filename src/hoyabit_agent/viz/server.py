"""軌跡前端的最小 web server —— 讓評審輸入回合識別碼看到推理過程。

`create_app` 接受一個 `AnalysisStore`（依賴注入），因此測試可以塞記憶體 store，
不必起 Postgres。真的要跑時 `serve()` 會接上真實的 Postgres store。

呈現交給 `trace_html` 的純函數，這一層只負責路由與從 store 取資料。
"""

from __future__ import annotations

import html

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from hoyabit_agent.seams import AnalysisStore
from hoyabit_agent.viz.trace_html import render_outcome, trace_json


def create_app(store: AnalysisStore) -> Starlette:
    async def index(request: Request) -> Response:
        run_ids = await store.recent()
        if not run_ids:
            body = (
                "<p class=empty>還沒有任何分析回合。跑 "
                "<code>python -m hoyabit_agent BTC --live --save</code> 產生一筆。</p>"
            )
        else:
            links = "".join(
                f'<li><a href="/run/{html.escape(rid)}">{html.escape(rid)}</a></li>'
                for rid in run_ids
            )
            body = f"<ul class=runs>{links}</ul>"
        return HTMLResponse(_INDEX.format(body=body))

    async def show_run(request: Request) -> Response:
        run_id = request.path_params["run_id"]
        outcome = await store.load(run_id)
        if outcome is None:
            return HTMLResponse(
                _INDEX.format(body=f"<p class=empty>找不到回合 {html.escape(run_id)}。</p>"),
                status_code=404,
            )
        return HTMLResponse(render_outcome(outcome))

    async def run_json(request: Request) -> Response:
        run_id = request.path_params["run_id"]
        outcome = await store.load(run_id)
        if outcome is None:
            return JSONResponse({"error": "not found", "run_id": run_id}, status_code=404)
        return Response(trace_json(outcome), media_type="application/json")

    return Starlette(
        routes=[
            Route("/", index),
            Route("/run/{run_id}", show_run),
            Route("/run/{run_id}/trace.json", run_json),
        ]
    )


_INDEX = """<!doctype html>
<html lang=zh-Hant><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>推論軌跡</title>
<style>
body{{font:15px/1.6 system-ui,"Noto Sans TC",sans-serif;max-width:640px;margin:40px auto;
  padding:0 16px;color:#111827}}
h1{{font-size:20px}}
ul.runs{{list-style:none;padding:0}}
ul.runs li{{padding:8px 0;border-bottom:1px solid #eee}}
ul.runs a{{font:14px monospace;color:#2563eb;text-decoration:none}}
.empty{{color:#6b7280}}
code{{background:#f3f4f6;padding:1px 6px;border-radius:4px}}
</style></head><body>
<h1>推論軌跡</h1>{body}
</body></html>"""


async def serve(host: str = "127.0.0.1", port: int = 8000) -> None:  # pragma: no cover - I/O
    """以真實 Postgres store 起 server。"""
    import uvicorn

    from hoyabit_agent.storage.postgres import PostgresAnalysisStore

    app = create_app(PostgresAnalysisStore.from_environment())
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    await uvicorn.Server(config).serve()


def main() -> int:  # pragma: no cover - 進入點
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="啟動推論軌跡前端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    asyncio.run(serve(args.host, args.port))
    return 0


__all__ = ["create_app", "main", "serve"]
