"""極小的 `.env` 載入器；既有程序環境永遠優先。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

_T = TypeVar("_T")


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "a").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def run_async(coroutine: Coroutine[Any, Any, _T]) -> _T:
    """在 Windows 使用 psycopg 相容的 Selector event loop。"""
    if os.name == "nt":
        return asyncio.Runner(loop_factory=asyncio.SelectorEventLoop).run(coroutine)
    return asyncio.run(coroutine)


__all__ = ["load_dotenv", "run_async"]
