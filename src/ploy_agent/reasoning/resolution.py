from __future__ import annotations

import json
import re

from ploy_agent.common.config import settings


_AMBIGUOUS = re.compile(
    r"\b("
    r"at the discretion|"
    r"twitter\.com|x\.com/|reddit\.com|wikipedia\.org|"
    r"twitter\s+announcement|twitter\s+post|twitter\s+account|"
    r"reddit\s+discussion|reddit\s+thread|reddit\s+post|"
    r"(?:based on|per|according to)\s+(?:official|twitter|reddit|wikipedia)|"
    r"officially\s+announced|"
    r"according\s+to\b|"
    r"third[- ]party\s+source|external\s+source|news\s+article\s+confirms"
    r")\b",
    re.IGNORECASE,
)


def heuristic_resolution_safe(text: str | None) -> tuple[bool, str]:
    if not text:
        return True, "empty_criteria"
    if _AMBIGUOUS.search(text):
        return False, "ambiguous_keywords"
    return True, "heuristic_ok"


_async_anthropic_client = None


def _get_async_anthropic():
    global _async_anthropic_client
    if _async_anthropic_client is None:
        import anthropic
        _async_anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _async_anthropic_client


async def resolution_gate(text: str | None) -> tuple[bool, str]:
    """Heuristic + optional async Anthropic classification."""
    safe, reason = heuristic_resolution_safe(text)
    if not safe:
        return safe, reason
    if not settings.anthropic_api_key:
        return safe, reason
    try:
        ac = _get_async_anthropic()
        msg = await ac.messages.create(
            model=settings.anthropic_model,
            max_tokens=200,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "You classify Polymarket resolution criteria for dispute risk.\n"
                        "Reply JSON only: {\"safe\": true|false, \"reason\": \"...\"}.\n"
                        "safe=false if criteria rely on unofficial sources, subjective wording, "
                        "or could be disputed.\n\nCRITERIA:\n"
                        + (text or "")
                    ),
                }
            ],
        )
        block = msg.content[0]
        if block.type != "text":
            return safe, "llm_unexpected_block"
        raw = block.text.strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < 0:
            return safe, "llm_parse_fallback"
        m = json.loads(raw[start : end + 1])
        return bool(m.get("safe", True)), str(m.get("reason", "llm"))
    except Exception as e:
        h = heuristic_resolution_safe(text)
        return h[0], f"llm_failed:{e}"
