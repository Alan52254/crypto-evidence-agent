"""In-process replayable event broker for live Agent trace streaming."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from hoyabit_agent.domain import TraceNode
from hoyabit_agent.trace_contract import trace_node_record


@dataclass
class _RunChannel:
    history: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    subscribers: set[asyncio.Queue[tuple[str, dict[str, Any]]]] = field(default_factory=set)
    terminal: bool = False


class RuntimeEventBroker:
    """Fan out events while retaining history for late EventSource subscribers."""

    def __init__(self) -> None:
        self._channels: dict[str, _RunChannel] = {}

    def begin(self, run_id: str) -> None:
        self._channels[run_id] = _RunChannel()

    def publish_trace(self, run_id: str, node: TraceNode) -> None:
        self._publish(run_id, "trace", trace_node_record(run_id, node))

    def complete(self, run_id: str, payload: dict[str, Any]) -> None:
        self._publish(run_id, "complete", payload, terminal=True)

    def fail(self, run_id: str, reason: str) -> None:
        self._publish(run_id, "error", {"run_id": run_id, "error": reason}, terminal=True)

    def _publish(
        self, run_id: str, event: str, payload: dict[str, Any], *, terminal: bool = False
    ) -> None:
        channel = self._channels.setdefault(run_id, _RunChannel())
        item = (event, payload)
        channel.history.append(item)
        channel.terminal = channel.terminal or terminal
        for queue in tuple(channel.subscribers):
            queue.put_nowait(item)

    async def stream(self, run_id: str) -> AsyncIterator[str]:
        channel = self._channels.get(run_id)
        if channel is None:
            yield _sse("error", {"run_id": run_id, "error": "unknown run"})
            return
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        snapshot = tuple(channel.history)
        for event, payload in snapshot:
            yield _sse(event, payload)
        if channel.terminal:
            return
        channel.subscribers.add(queue)
        try:
            while True:
                try:
                    event, payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    # SSE keepalive — 防止瀏覽器/proxy 判定連線死亡
                    yield ": keepalive\n\n"
                    continue
                yield _sse(event, payload)
                if event in {"complete", "error"}:
                    return
        finally:
            channel.subscribers.discard(queue)


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


__all__ = ["RuntimeEventBroker"]
