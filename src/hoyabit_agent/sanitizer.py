"""Sanitizer module for filtering malicious user input and preventing Prompt Injection attacks."""

from __future__ import annotations

import re

# Direct and Indirect Prompt Injection Patterns
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous\s+)?(instructions|rules|directives|constraints)",
    r"override\s+(all\s+)?(previous\s+)?(rules|directives|instructions|constraints)",
    r"system\s+prompt\s*:",
    r"forget\s+all\s+(instructions|rules)",
    r"you\s+are\s+now\s+a\s+rogue",
    r"bypass\s+security",
    r"disregard\s+above",
    r"jailbreak",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def sanitize_user_question(raw_question: str) -> str:
    """Sanitize natural language user queries to strip prompt injection patterns.

    Guarantees user input cannot overwrite system directives or hijack LLM control flows.
    """
    if not raw_question:
        return raw_question

    cleaned = raw_question
    for pattern in _COMPILED_PATTERNS:
        cleaned = pattern.sub("[FILTERED_INSTRUCTION]", cleaned)

    return cleaned


__all__ = ["sanitize_user_question"]
