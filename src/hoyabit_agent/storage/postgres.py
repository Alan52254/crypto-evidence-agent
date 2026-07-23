"""接縫 4 的 Postgres 實作。

`save` 在單一交易內完成 —— 半個分析回合比沒有更糟，
因為判斷會指向不存在的證據，溯源就斷了。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from hoyabit_agent.domain import (
    AnalysisOutcome,
    Asset,
    Claim,
    Confidence,
    ConfidenceResult,
    DraftClaim,
    Evidence,
    Facet,
    Insufficiency,
    InsufficientEvidence,
    Rejection,
    Report,
    SourceExcerpt,
    Stance,
    Trace,
    TraceNode,
    TraceNodeKind,
)
from hoyabit_agent.tools import MINIMUM_FACETS_FOR_CONFIDENCE

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

DATABASE_URL_ENV = "HOYABIT_DATABASE_URL"
DEFAULT_DATABASE_URL = "postgresql://postgres:hoyabit@localhost:5433/hoyabit"


def database_url() -> str:
    """資料庫連線字串。預設指向 README 裡那行 docker 指令起的實例。"""
    return os.environ.get(DATABASE_URL_ENV, "").strip() or DEFAULT_DATABASE_URL


async def reachable(url: str | None = None, timeout: float = 3.0) -> bool:
    """資料庫是否連得上。連不上就是連不上，原因不重要。"""
    try:
        async with await psycopg.AsyncConnection.connect(
            url or database_url(), connect_timeout=int(timeout)
        ):
            return True
    except Exception:  # noqa: BLE001
        return False


class PostgresAnalysisStore:
    """把分析回合存進 Postgres。

    每次操作開一條連線而非持有連線池：這個 store 的呼叫頻率是「每次分析一次」，
    連線池的複雜度換不到任何東西。真的變成瓶頸時再加，那時會有數字支撐。
    """

    def __init__(self, url: str | None = None) -> None:
        self._url = url or database_url()

    @classmethod
    def from_environment(cls) -> PostgresAnalysisStore:
        """依 `HOYABIT_DATABASE_URL` 建構。"""
        return cls(database_url())

    async def _connect(self) -> psycopg.AsyncConnection[Any]:
        return await psycopg.AsyncConnection.connect(self._url, row_factory=dict_row)

    async def migrate(self) -> None:
        """建立 schema。可重複執行。"""
        async with await self._connect() as connection:
            await connection.execute(SCHEMA)
            await connection.commit()

    async def reset(self) -> None:
        """清空並重建 schema。**只給測試用。**"""
        async with await self._connect() as connection:
            await connection.execute(
                "DROP TABLE IF EXISTS trace_node, claim, source_excerpt, evidence,"
                " analysis_run CASCADE"
            )
            await connection.execute(SCHEMA)
            await connection.commit()

    async def close(self) -> None:
        """目前不持有資源；保留這個方法讓呼叫端不必知道這件事。"""
        return None

    # -- 寫入 -----------------------------------------------------------

    async def save(self, outcome: AnalysisOutcome) -> None:
        """在單一交易內寫入整個回合。同一個識別碼再存一次會覆蓋，不會重複。"""
        await self.migrate()
        async with await self._connect() as connection:
            async with connection.transaction():
                # 冪等：先刪再寫。外鍵 ON DELETE CASCADE 會清掉所有從屬資料。
                await connection.execute(
                    "DELETE FROM analysis_run WHERE run_id = %s", (outcome.run_id,)
                )
                await self._insert_run(connection, outcome)
                if outcome.report is not None:
                    await self._insert_evidence(connection, outcome.run_id, outcome.report)
                    await self._insert_claims(connection, outcome.run_id, outcome.report)
                await self._insert_trace(connection, outcome.trace)
            await connection.commit()

    async def _insert_run(
        self, connection: psycopg.AsyncConnection[Any], outcome: AnalysisOutcome
    ) -> None:
        report = outcome.report
        confidence = report.confidence if report is not None else None
        value: float | None = None
        cause: str | None = None
        stances: dict[str, str] = {}
        present: list[str] = []

        if isinstance(confidence, Confidence | InsufficientEvidence):
            stances = {
                facet.value: stance.value
                for facet, stance in confidence.facet_stances.items()
            }
        if isinstance(confidence, Confidence):
            value = confidence.value
        elif isinstance(confidence, InsufficientEvidence):
            cause = confidence.cause.value
            present = sorted(facet.value for facet in confidence.facets_present)

        await connection.execute(
            "INSERT INTO analysis_run (run_id, asset, stance, rejection_reason,"
            " confidence_value, confidence_cause, confidence_facet_stances, facets_present)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                outcome.run_id,
                report.asset.value if report is not None else None,
                report.stance.value if report is not None else None,
                outcome.rejection.reason if outcome.rejection is not None else None,
                value,
                cause,
                json.dumps(stances),
                json.dumps(present),
            ),
        )

    async def _insert_evidence(
        self, connection: psycopg.AsyncConnection[Any], run_id: str, report: Report
    ) -> None:
        for index, item in enumerate(report.evidence):
            await connection.execute(
                "INSERT INTO evidence (run_id, evidence_id, facet, summary, stance_hint,"
                " event_key, position) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (run_id, item.id, item.facet.value, item.summary, item.stance_hint,
                 item.event_key, index),
            )
            for offset, excerpt in enumerate(item.excerpts):
                await connection.execute(
                    "INSERT INTO source_excerpt (run_id, evidence_id, source_id, url,"
                    " retrieved_at, locator, text, position)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (run_id, item.id, excerpt.source_id, excerpt.url, excerpt.retrieved_at,
                     excerpt.locator, excerpt.text, offset),
                )

    async def _insert_claims(
        self, connection: psycopg.AsyncConnection[Any], run_id: str, report: Report
    ) -> None:
        rows = [(claim.text, claim.facet, claim.evidence_ids, True) for claim in report.claims]
        rows += [
            (draft.text, draft.facet, draft.evidence_ids, False)
            for draft in report.dropped_claims
        ]
        for index, (text, facet, ids, kept) in enumerate(rows):
            await connection.execute(
                "INSERT INTO claim (run_id, text, facet, evidence_ids, kept, position)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (run_id, text, facet.value, json.dumps(list(ids)), kept, index),
            )

    async def _insert_trace(
        self, connection: psycopg.AsyncConnection[Any], trace: Trace
    ) -> None:
        for node in trace.nodes:
            await connection.execute(
                "INSERT INTO trace_node (run_id, seq, kind, reason, evidence_ids,"
                " gap_before, gap_after, elapsed_seconds, detail)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    trace.run_id,
                    node.seq,
                    node.kind.value,
                    node.reason,
                    json.dumps(list(node.evidence_ids)),
                    json.dumps(sorted(facet.value for facet in node.gap_before)),
                    json.dumps(sorted(facet.value for facet in node.gap_after)),
                    node.elapsed_seconds,
                    json.dumps(dict(node.detail)),
                ),
            )

    # -- 讀取 -----------------------------------------------------------

    async def load(self, run_id: str) -> AnalysisOutcome | None:
        await self.migrate()
        async with await self._connect() as connection:
            run = await self._fetch_one(
                connection, "SELECT * FROM analysis_run WHERE run_id = %s", (run_id,)
            )
            if run is None:
                return None

            trace = Trace(
                run_id=run_id,
                nodes=tuple(
                    _to_node(row)
                    for row in await self._fetch_all(
                        connection,
                        "SELECT * FROM trace_node WHERE run_id = %s ORDER BY seq",
                        (run_id,),
                    )
                ),
            )

            rejection = (
                Rejection(reason=run["rejection_reason"]) if run["rejection_reason"] else None
            )
            if run["asset"] is None:
                return AnalysisOutcome(
                    run_id=run_id, report=None, trace=trace, rejection=rejection
                )

            evidence = await self._load_evidence(connection, run_id)
            kept, dropped = await self._load_claims(connection, run_id)
            return AnalysisOutcome(
                run_id=run_id,
                report=Report(
                    asset=Asset(run["asset"]),
                    stance=Stance(run["stance"]),
                    confidence=_to_confidence(run),
                    claims=kept,
                    dropped_claims=dropped,
                    evidence=evidence,
                ),
                trace=trace,
                rejection=rejection,
            )

    async def recent(self, limit: int = 20) -> tuple[str, ...]:
        await self.migrate()
        async with await self._connect() as connection:
            rows = await self._fetch_all(
                connection,
                "SELECT run_id FROM analysis_run ORDER BY created_at DESC, run_id DESC LIMIT %s",
                (limit,),
            )
        return tuple(str(row["run_id"]) for row in rows)

    async def _load_evidence(
        self, connection: psycopg.AsyncConnection[Any], run_id: str
    ) -> tuple[Evidence, ...]:
        rows = await self._fetch_all(
            connection, "SELECT * FROM evidence WHERE run_id = %s ORDER BY position", (run_id,)
        )
        excerpts = await self._fetch_all(
            connection,
            "SELECT * FROM source_excerpt WHERE run_id = %s ORDER BY evidence_id, position",
            (run_id,),
        )
        grouped: dict[str, list[SourceExcerpt]] = {}
        for row in excerpts:
            grouped.setdefault(str(row["evidence_id"]), []).append(
                SourceExcerpt(
                    source_id=row["source_id"],
                    url=row["url"],
                    retrieved_at=row["retrieved_at"],
                    locator=row["locator"],
                    text=row["text"],
                )
            )
        return tuple(
            Evidence(
                id=str(row["evidence_id"]),
                facet=Facet(row["facet"]),
                summary=row["summary"],
                stance_hint=row["stance_hint"],
                excerpts=tuple(grouped.get(str(row["evidence_id"]), [])),
                event_key=row["event_key"],
            )
            for row in rows
        )

    async def _load_claims(
        self, connection: psycopg.AsyncConnection[Any], run_id: str
    ) -> tuple[tuple[Claim, ...], tuple[DraftClaim, ...]]:
        rows = await self._fetch_all(
            connection, "SELECT * FROM claim WHERE run_id = %s ORDER BY position", (run_id,)
        )
        kept = tuple(
            Claim(row["text"], tuple(row["evidence_ids"]), Facet(row["facet"]))
            for row in rows
            if row["kept"]
        )
        dropped = tuple(
            DraftClaim(row["text"], tuple(row["evidence_ids"]), Facet(row["facet"]))
            for row in rows
            if not row["kept"]
        )
        return kept, dropped

    @staticmethod
    async def _fetch_all(
        connection: psycopg.AsyncConnection[Any], sql: str, params: tuple[Any, ...]
    ) -> list[dict[str, Any]]:
        async with connection.cursor() as cursor:
            await cursor.execute(sql, params)
            return list(await cursor.fetchall())

    @staticmethod
    async def _fetch_one(
        connection: psycopg.AsyncConnection[Any], sql: str, params: tuple[Any, ...]
    ) -> dict[str, Any] | None:
        async with connection.cursor() as cursor:
            await cursor.execute(sql, params)
            return await cursor.fetchone()


def _to_node(row: dict[str, Any]) -> TraceNode:
    return TraceNode(
        seq=row["seq"],
        kind=TraceNodeKind(row["kind"]),
        reason=row["reason"],
        evidence_ids=tuple(row["evidence_ids"]),
        gap_before=frozenset(Facet(value) for value in row["gap_before"]),
        gap_after=frozenset(Facet(value) for value in row["gap_after"]),
        elapsed_seconds=row["elapsed_seconds"],
        detail=dict(row["detail"]),
    )


def _to_confidence(row: dict[str, Any]) -> ConfidenceResult:
    stances = {Facet(k): Stance(v) for k, v in (row["confidence_facet_stances"] or {}).items()}
    if row["confidence_value"] is not None:
        return Confidence(value=row["confidence_value"], facet_stances=stances)
    return InsufficientEvidence(
        facets_present=frozenset(Facet(value) for value in (row["facets_present"] or [])),
        minimum_facets_required=MINIMUM_FACETS_FOR_CONFIDENCE,
        cause=Insufficiency(row["confidence_cause"] or Insufficiency.TOO_FEW_FACETS.value),
        facet_stances=stances,
    )


__all__ = [
    "DATABASE_URL_ENV",
    "DEFAULT_DATABASE_URL",
    "SCHEMA",
    "PostgresAnalysisStore",
    "database_url",
    "reachable",
]
