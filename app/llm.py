"""Claude client wrapper: streaming answers and schema-validated JSON.

Request shape notes:
- The system prompt is sent as a cacheable block. Once the stable prefix passes
  ~1024 tokens the API serves it from cache on repeat calls; below that the
  cache_control is simply ignored, so it costs nothing to leave in.
- Retrieved excerpts go in the user turn, never in ``system`` - they change on
  every question and would invalidate the cached prefix.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import anthropic

from .config import get_settings

logger = logging.getLogger(__name__)

# Server-side refusal fallback (Claude API only). Flipped off for the process if
# the org has not enabled the beta, so one bad request doesn't break every chat.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_fallbacks_enabled: bool | None = None

_REFUSAL_MESSAGE = (
    "I wasn't able to answer that one. Try rephrasing the question, or ask about a "
    "specific part of the placement you want to understand."
)


class LLMNotConfigured(RuntimeError):
    pass


_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise LLMNotConfigured(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key "
                "from https://console.anthropic.com/settings/keys"
            )
        _client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=120.0)
    return _client


def is_configured() -> bool:
    return bool(get_settings().anthropic_api_key)


def _use_fallbacks() -> bool:
    global _fallbacks_enabled
    if _fallbacks_enabled is None:
        _fallbacks_enabled = get_settings().anthropic_server_fallbacks
    return _fallbacks_enabled


def _disable_fallbacks(reason: str) -> None:
    global _fallbacks_enabled
    if _fallbacks_enabled:
        logger.warning("Disabling server-side refusal fallbacks for this process: %s", reason)
    _fallbacks_enabled = False


def _system_blocks(system: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _base_kwargs(system: str, messages: list[dict[str, Any]], effort: str | None) -> dict[str, Any]:
    settings = get_settings()
    return {
        "model": settings.anthropic_model,
        "system": _system_blocks(system),
        "messages": messages,
        "thinking": {"type": "adaptive", "display": settings.anthropic_thinking_display},
        "output_config": {"effort": effort or settings.anthropic_effort},
    }


def _is_beta_rejection(exc: anthropic.BadRequestError) -> bool:
    text = str(getattr(exc, "message", "") or exc).lower()
    return "fallback" in text or "beta" in text


async def stream_answer(
    system: str,
    messages: list[dict[str, Any]],
    *,
    effort: str | None = None,
    max_tokens: int = 64000,
) -> AsyncIterator[dict[str, Any]]:
    """Yield ``{"type": ...}`` events: ``thinking``, ``text``, ``done``, ``error``.

    Streaming rather than a single response because answers can be long and a
    chat UI should show progress rather than a spinner.
    """
    client = get_client()
    kwargs = _base_kwargs(system, messages, effort) | {"max_tokens": max_tokens}

    for attempt in (1, 2):
        use_fallbacks = _use_fallbacks()
        try:
            if use_fallbacks:
                stream_ctx = client.beta.messages.stream(
                    **kwargs, betas=[_FALLBACK_BETA], fallbacks="default"
                )
            else:
                stream_ctx = client.messages.stream(**kwargs)

            async with stream_ctx as stream:
                async for event in stream:
                    if event.type != "content_block_delta":
                        continue
                    delta = event.delta
                    if delta.type == "thinking_delta":
                        yield {"type": "thinking", "text": delta.thinking}
                    elif delta.type == "text_delta":
                        yield {"type": "text", "text": delta.text}

                final = await stream.get_final_message()

            # Check the stop reason before trusting the content.
            if final.stop_reason == "refusal":
                category = getattr(getattr(final, "stop_details", None), "category", None)
                logger.info("Request declined by safety classifier (category=%s)", category)
                yield {"type": "text", "text": _REFUSAL_MESSAGE}

            usage = final.usage
            yield {
                "type": "done",
                "stop_reason": final.stop_reason,
                "usage": {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
                    "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
                },
            }
            return

        except anthropic.BadRequestError as exc:
            if attempt == 1 and use_fallbacks and _is_beta_rejection(exc):
                _disable_fallbacks(str(exc))
                continue
            logger.exception("Bad request to Claude")
            yield {"type": "error", "message": f"Request rejected: {exc.message}"}
            return
        except anthropic.AuthenticationError:
            yield {"type": "error", "message": "ANTHROPIC_API_KEY was rejected. Check the key in .env."}
            return
        except anthropic.RateLimitError as exc:
            retry_after = exc.response.headers.get("retry-after", "60") if exc.response else "60"
            yield {"type": "error", "message": f"Rate limited by the API. Try again in {retry_after}s."}
            return
        except anthropic.APIStatusError as exc:
            logger.exception("Claude API error")
            hint = "The API is having trouble - try again shortly." if exc.status_code >= 500 else exc.message
            yield {"type": "error", "message": f"API error ({exc.status_code}): {hint}"}
            return
        except anthropic.APIConnectionError:
            yield {"type": "error", "message": "Could not reach the Claude API. Check the network connection."}
            return


async def structured(
    system: str,
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
    *,
    effort: str | None = None,
    max_tokens: int = 16000,
) -> dict[str, Any]:
    """One call that must return JSON matching ``schema``.

    Used where the app needs fields rather than prose - interview questions and
    scored feedback both drive UI elements.
    """
    client = get_client()
    kwargs = _base_kwargs(system, messages, effort) | {"max_tokens": max_tokens}
    kwargs["output_config"]["format"] = {"type": "json_schema", "schema": schema}

    for attempt in (1, 2):
        use_fallbacks = _use_fallbacks()
        try:
            if use_fallbacks:
                response = await client.beta.messages.create(
                    **kwargs, betas=[_FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = await client.messages.create(**kwargs)

            if response.stop_reason == "refusal":
                raise RuntimeError(_REFUSAL_MESSAGE)

            text = next((b.text for b in response.content if b.type == "text"), "")
            if not text:
                raise RuntimeError("Claude returned no content for a structured request.")
            return json.loads(text)

        except anthropic.BadRequestError as exc:
            if attempt == 1 and use_fallbacks and _is_beta_rejection(exc):
                _disable_fallbacks(str(exc))
                continue
            raise
    raise RuntimeError("unreachable")
