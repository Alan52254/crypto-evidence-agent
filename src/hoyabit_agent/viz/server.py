"""軌跡前端的最小 web server —— 讓評審輸入回合識別碼看到推理過程。

`create_app` 接受一個 `AnalysisStore`（依賴注入），因此測試可以塞記憶體 store，
不必起 Postgres。真的要跑時 `serve()` 會接上真實的 Postgres store。

呈現交給 `trace_html` 的純函數，這一層只負責路由與從 store 取資料。
"""

from __future__ import annotations

import asyncio
import html
import uuid
from collections.abc import Awaitable, Callable
from datetime import date

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from hoyabit_agent.api_contract import outcome_payload
from hoyabit_agent.config import run_async
from hoyabit_agent.domain import AnalysisOutcome, AnalysisRequest, TraceNode
from hoyabit_agent.runtime_events import RuntimeEventBroker
from hoyabit_agent.seams import AnalysisStore
from hoyabit_agent.storage.cache_dynamodb import get_cache
from hoyabit_agent.viz.trace_html import render_outcome, trace_json

AnalysisRunner = Callable[
    [AnalysisRequest, str, Callable[[TraceNode], None]], Awaitable[AnalysisOutcome]
]


def create_app(
    store: AnalysisStore,
    runner: AnalysisRunner | None = None,
    broker: RuntimeEventBroker | None = None,
) -> Starlette:
    event_broker = broker or RuntimeEventBroker()
    tasks: set[asyncio.Task[None]] = set()

    async def index(request: Request) -> Response:
        try:
            run_ids = await store.recent()
        except Exception:  # noqa: BLE001 — 資料庫連不到不該讓整頁 500，降級成提示
            return HTMLResponse(
                _INDEX.format(body=_DB_UNAVAILABLE), status_code=503
            )
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
        try:
            outcome = await store.load(run_id)
        except Exception:  # noqa: BLE001 — 見 index() 的說明
            return HTMLResponse(
                _INDEX.format(body=_DB_UNAVAILABLE), status_code=503
            )
        if outcome is None:
            return HTMLResponse(
                _INDEX.format(body=f"<p class=empty>找不到回合 {html.escape(run_id)}。</p>"),
                status_code=404,
            )
        return HTMLResponse(render_outcome(outcome))

    async def run_json(request: Request) -> Response:
        run_id = request.path_params["run_id"]
        try:
            outcome = await store.load(run_id)
        except Exception:  # noqa: BLE001 — 見 index() 的說明
            return JSONResponse({"error": "database unavailable"}, status_code=503)
        if outcome is None:
            return JSONResponse({"error": "not found", "run_id": run_id}, status_code=404)
        return Response(trace_json(outcome), media_type="application/json")

    async def start_analysis(request: Request) -> Response:
        if runner is None:
            return JSONResponse({"error": "analysis runner unavailable"}, status_code=503)
        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        asset = str(payload.get("asset", "")).strip().upper()
        question = str(payload.get("question", "")).strip()
        if asset not in {"BTC", "ETH", "SOL", "BNB", "XRP"} or not question:
            return JSONResponse({"error": "asset and question are required"}, status_code=422)

        # Session Memory — 載入歷史對話，實現多輪上下文
        session_id = str(payload.get("session_id", "")).strip() or None
        cache = get_cache()
        context_prefix = ""
        if session_id:
            history = cache.get_session_memory(session_id)
            if history:
                context_lines = [
                    f"[{msg['role']}]: {msg['content']}" for msg in history[-10:]
                ]
                context_prefix = (
                    "以下是先前的對話歷史（供上下文參考）：\n"
                    + "\n".join(context_lines)
                    + "\n\n當前問題：\n"
                )
            # 儲存本次使用者提問
            cache.save_session_memory(session_id, "user", question)

        # 帶入歷史上下文的完整問題
        enriched_question = context_prefix + question if context_prefix else question

        # 這是即時 demo 端點 —— 未帶 as_of_date 時預設今天,落在即時模式,
        # 讓 live 證據源真的會被呼叫。若靜默退回資料集截止日,回測 regime
        # 會把所有 live 來源濾掉(Step 4),使用者看不出來,系統卻什麼都沒抓。
        raw_as_of = payload.get("as_of_date")
        try:
            as_of_date = date.fromisoformat(str(raw_as_of)) if raw_as_of else date.today()
        except ValueError:
            return JSONResponse({"error": "as_of_date must be an ISO date"}, status_code=422)

        run_id = str(uuid.uuid4())
        event_broker.begin(run_id)

        async def execute() -> None:
            try:
                outcome = await runner(
                    AnalysisRequest(asset, enriched_question, as_of_date=as_of_date),
                    run_id,
                    lambda node: event_broker.publish_trace(run_id, node),
                )
                await store.save(outcome)
                event_broker.complete(run_id, outcome_payload(outcome))

                # 儲存 assistant 回應至 session memory
                if session_id and outcome.report is not None:
                    summary = outcome.report.summary if hasattr(outcome.report, 'summary') else str(outcome.report)
                    cache.save_session_memory(session_id, "assistant", summary[:2000])
            except Exception as exc:  # noqa: BLE001 — becomes a terminal SSE event
                event_broker.fail(run_id, f"{type(exc).__name__}: {exc}")

        task = asyncio.create_task(execute())
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return JSONResponse(
            {"run_id": run_id, "stream_url": f"/api/v1/stream_trace?run_id={run_id}"},
            status_code=202,
        )

    async def stream_trace(request: Request) -> Response:
        run_id = request.query_params.get("run_id", "")
        return StreamingResponse(
            event_broker.stream(run_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def _load_or_503(run_id: str) -> AnalysisOutcome | JSONResponse:
        """讀一個回合，資料庫連不到時回傳 503 而非讓例外炸穿到 ASGI 層。"""
        try:
            return await store.load(run_id)  # type: ignore[return-value]
        except Exception:  # noqa: BLE001 — 見 index() 的說明
            return JSONResponse({"error": "database unavailable"}, status_code=503)

    async def run_api(request: Request) -> Response:
        run_id = request.path_params["run_id"]
        outcome = await _load_or_503(run_id)
        if isinstance(outcome, JSONResponse):
            return outcome
        if outcome is None:
            return JSONResponse({"error": "not found", "run_id": run_id}, status_code=404)
        return JSONResponse(outcome_payload(outcome))

    async def evidence_json(request: Request) -> Response:
        run_id = request.path_params["run_id"]
        outcome = await _load_or_503(run_id)
        if isinstance(outcome, JSONResponse):
            return outcome
        if outcome is None or outcome.report is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        from hoyabit_agent.artifacts import evidence_list
        return JSONResponse(evidence_list(outcome.report))

    async def execution_log_endpoint(request: Request) -> Response:
        run_id = request.path_params["run_id"]
        outcome = await _load_or_503(run_id)
        if isinstance(outcome, JSONResponse):
            return outcome
        if outcome is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        from hoyabit_agent.artifacts import execution_log_jsonl
        return Response(execution_log_jsonl(outcome), media_type="application/x-ndjson")

    async def manifest_endpoint(request: Request) -> Response:
        run_id = request.path_params["run_id"]
        outcome = await _load_or_503(run_id)
        if isinstance(outcome, JSONResponse):
            return outcome
        if outcome is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        from hoyabit_agent.artifacts import output_manifest
        return JSONResponse(output_manifest(outcome))

    async def health_endpoint(request: Request) -> Response:
        return JSONResponse({"status": "ok", "service": "hoyabit-agent"})

    async def list_runs_api(request: Request) -> Response:
        try:
            run_ids = await store.recent(limit=20)
            runs = []
            for rid in run_ids:
                try:
                    outcome = await store.load(rid)
                    if outcome is not None:
                        runs.append(outcome_payload(outcome))
                except Exception:  # noqa: BLE001
                    pass
            return JSONResponse({"runs": runs})
        except Exception:  # noqa: BLE001
            return JSONResponse({"runs": []})

    async def export_artifacts(request: Request) -> Response:
        """POST /api/v1/export-artifacts — 打包 PDF + 證據 + 日誌 + 配置為 ZIP。"""
        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)

        run_id = str(payload.get("run_id", "")).strip()
        if not run_id:
            return JSONResponse({"error": "run_id is required"}, status_code=422)

        outcome = await store.load(run_id)
        if outcome is None:
            return JSONResponse({"error": "not found", "run_id": run_id}, status_code=404)

        session_id = str(payload.get("session_id", "")).strip() or None

        from hoyabit_agent.download_manager import build_export_zip, export_filename
        zip_bytes = build_export_zip(outcome, session_id=session_id)
        filename = export_filename(session_id)

        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(zip_bytes)),
            },
        )

    return Starlette(
        routes=[
            Route("/", index),
            Route("/health", health_endpoint),
            Route("/run/{run_id}", show_run),
            Route("/run/{run_id}/trace.json", run_json),
            Route("/api/v1/health", health_endpoint),
            Route("/api/v1/runs", list_runs_api),
            Route("/api/v1/analyse", start_analysis, methods=["POST"]),
            Route("/api/v1/stream_trace", stream_trace),
            Route("/api/v1/export-artifacts", export_artifacts, methods=["POST"]),
            Route("/api/v1/runs/{run_id}", run_api),
            Route("/api/v1/runs/{run_id}/evidence", evidence_json),
            Route("/api/v1/runs/{run_id}/logs", execution_log_endpoint),
            Route("/api/v1/runs/{run_id}/manifest", manifest_endpoint),
        ]
    )


_DB_UNAVAILABLE = (
    "<p class=empty>連不到資料庫（Postgres + pgvector）。分析結果沒有地方存取，"
    "無法列出或讀取回合。</p>"
    "<p class=empty>先確認資料庫在跑：<code>docker start hoyabit-pg</code>"
    "（或依 README 的指令重新建立），再重新整理這頁。</p>"
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


class ResilientAnalysisStore:
    """自動容錯 Store：當 Postgres 連不上時，自動降級使用記憶體 Store。"""

    def __init__(self, primary: AnalysisStore, fallback: AnalysisStore, is_online: bool = True) -> None:
        self._primary = primary
        self._fallback = fallback
        self.is_online = is_online

    async def save(self, outcome: AnalysisOutcome) -> None:
        if self.is_online:
            try:
                await self._primary.save(outcome)
            except Exception:  # noqa: BLE001
                self.is_online = False
        await self._fallback.save(outcome)

    async def load(self, run_id: str) -> AnalysisOutcome | None:
        if self.is_online:
            try:
                result = await self._primary.load(run_id)
                if result is not None:
                    return result
            except Exception:  # noqa: BLE001
                self.is_online = False
        return await self._fallback.load(run_id)

    async def recent(self, limit: int = 20) -> tuple[str, ...]:
        if self.is_online:
            try:
                results = await self._primary.recent(limit)
                if results:
                    return results
            except Exception:  # noqa: BLE001
                self.is_online = False
        return await self._fallback.recent(limit)


async def serve(host: str = "127.0.0.1", port: int = 8000) -> None:  # pragma: no cover - I/O
    """起軌跡前端 server（支援 Postgres 斷線自動降級）。"""
    import logging

    import uvicorn

    from hoyabit_agent.config import load_dotenv
    from hoyabit_agent.ingest.runtime import build_competition_sources
    from hoyabit_agent.models.factory import create_model_provider
    from hoyabit_agent.run import analyse
    from hoyabit_agent.storage.postgres import PostgresAnalysisStore, reachable
    from hoyabit_agent.testing import InMemoryAnalysisStore

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    load_dotenv()

    async def production_runner(
        request: AnalysisRequest,
        run_id: str,
        on_trace: Callable[[TraceNode], None],
    ) -> AnalysisOutcome:
        import httpx

        # 200s：留在 BedrockProvider 的 180s 內部 timeout 之上，
        # 讓 bedrock.py 的 asyncio.wait_for(timeout=180) 才是實際生效的上限——
        # 這裡若設得比它短，client 會在 180s 到之前就先炸掉 ReadTimeout，
        # 180s 的設定形同虛設（曾在真實 synthesise() 呼叫上重現：90s 就被腰斬 3 次）。
        async with httpx.AsyncClient(timeout=200.0) as client:
            model = await create_model_provider(client)
            if model is None:
                raise RuntimeError(
                    "No model provider configured (set GEMINI_API_KEY or GROQ_API_KEY)"
                )
            sources = await build_competition_sources(client, model)
            return await analyse(
                request, sources, model, run_id=run_id, on_trace=on_trace
            )

    is_online = await reachable(timeout=1.5)
    fallback_store = InMemoryAnalysisStore()
    postgres_store = PostgresAnalysisStore.from_environment()
    store = ResilientAnalysisStore(postgres_store, fallback_store, is_online=is_online)

    if is_online:
        logging.info("Connected to PostgreSQL analysis store.")
    else:
        logging.info("PostgreSQL unreachable on 5433/5432. Falling back to InMemoryAnalysisStore.")

    # AWS session check — Athena/Kinesis/Bedrock 全依賴 SSO token，
    # session 過期時三者都會報 LoginRefreshRequired 而非空結果。
    # 不在這裡阻擋啟動（Agent 的降級設計會跳過不可用來源），
    # 但明確告知操作者現在的狀態，避免等分析跑完才知道 AWS 掛了。
    try:
        import boto3
        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        logging.info(
            "AWS session OK — Account: %s, ARN: %s",
            identity.get("Account", "?"),
            identity.get("Arn", "?")[:60],
        )
    except Exception as aws_err:
        logging.warning(
            "AWS session EXPIRED or not configured: %s — "
            "Athena/Kinesis/Bedrock 工具將不可用。"
            "修復: 'aws sso login' 或設定 AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY",
            aws_err,
        )

    app = create_app(store, production_runner)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    await uvicorn.Server(config).serve()


def main() -> int:  # pragma: no cover - 進入點
    import argparse

    parser = argparse.ArgumentParser(description="啟動推論軌跡前端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run_async(serve(args.host, args.port))
    return 0


__all__ = ["create_app", "main", "serve"]
