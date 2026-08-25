"""Model client: streaming answers and schema-validated JSON.

Two providers behind one interface, chosen by ``LLM_PROVIDER``:

* ``gemini``    - Google Gemini. Free tier, self-signup key, hosted inference so
                  it runs fine on a 512MB Render instance. The default.
* ``anthropic`` - Claude. Paid, no free tier, stronger on nuanced interview
                  feedback. Kept so the choice is reversible without a rewrite.

Everything above this module calls ``stream_answer`` and ``structured`` and does
not care which is active.

Request shape notes:
- The system prompt is stable across turns, which is what makes prompt caching
  possible on Anthropic and keeps Gemini's prefix consistent.
- Retrieved excerpts go in the user turn, never the system prompt - they change
  on every question.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from .config import get_settings

logger = logging.getLogger(__name__)

_REFUSAL_MESSAGE = (
    "I wasn't able to answer that one. Try rephrasing the question, or ask about a "
    "specific part of the placement you want to understand."
)


class LLMNotConfigured(RuntimeError):
    pass


def provider() -> str:
    return get_settings().llm_provider.lower().strip() or "gemini"


def active_model() -> str:
    settings = get_settings()
    return settings.gemini_model if provider() == "gemini" else settings.anthropic_model


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.google_api_key if provider() == "gemini" else settings.anthropic_api_key)


def _missing_key_message() -> str:
    if provider() == "gemini":
        return (
            "GOOGLE_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey "
            "and add it to .env"
        )
    return (
        "ANTHROPIC_API_KEY is not set. Get a key at "
        "https://console.anthropic.com/settings/keys and add it to .env"
    )


# ===================================================================== Gemini

_gemini_client = None


def _get_gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai

        settings = get_settings()
        if not settings.google_api_key:
            raise LLMNotConfigured(_missing_key_message())
        _gemini_client = genai.Client(api_key=settings.google_api_key)
    return _gemini_client


def _gemini_contents(messages: list[dict[str, Any]]) -> list[Any]:
    """Map our message list onto Gemini's Content list.

    Gemini names the assistant role "model", and has no system role in
    ``contents`` - the system prompt goes in the config instead.
    """
    from google.genai import types

    contents = []
    for message in messages:
        role = "model" if message["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=message["content"])]))
    return contents


def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip keys Gemini's response_schema rejects.

    It accepts an OpenAPI subset - ``additionalProperties`` is not part of it and
    causes a 400.
    """
    if not isinstance(schema, dict):
        return schema
    cleaned = {k: v for k, v in schema.items() if k != "additionalProperties"}
    if "properties" in cleaned:
        cleaned["properties"] = {k: _gemini_schema(v) for k, v in cleaned["properties"].items()}
    if "items" in cleaned:
        cleaned["items"] = _gemini_schema(cleaned["items"])
    return cleaned


def _gemini_config(system: str, *, max_tokens: int, schema: dict[str, Any] | None = None):
    from google.genai import types

    settings = get_settings()
    kwargs: dict[str, Any] = {
        "system_instruction": system,
        "max_output_tokens": min(max_tokens, 65535),
    }
    if settings.gemini_show_thinking:
        kwargs["thinking_config"] = types.ThinkingConfig(include_thoughts=True)
    if schema is not None:
        kwargs["response_mime_type"] = "application/json"
        kwargs["response_schema"] = _gemini_schema(schema)
    return types.GenerateContentConfig(**kwargs)


def _gemini_parts(chunk: Any):
    """Yield (is_thought, text) for a streamed chunk, tolerating empty ones."""
    candidates = getattr(chunk, "candidates", None) or []
    if not candidates:
        return
    content = getattr(candidates[0], "content", None)
    for part in (getattr(content, "parts", None) or []):
        text = getattr(part, "text", None)
        if text:
            yield bool(getattr(part, "thought", False)), text


async def _gemini_stream(
    system: str, messages: list[dict[str, Any]], max_tokens: int
) -> AsyncIterator[dict[str, Any]]:
    from google.genai import errors

    client = _get_gemini()
    settings = get_settings()
    finish_reason = None
    usage = None

    try:
        stream = await client.aio.models.generate_content_stream(
            model=settings.gemini_model,
            contents=_gemini_contents(messages),
            config=_gemini_config(system, max_tokens=max_tokens),
        )
        async for chunk in stream:
            for is_thought, text in _gemini_parts(chunk):
                yield {"type": "thinking" if is_thought else "text", "text": text}
            if getattr(chunk, "candidates", None):
                finish_reason = getattr(chunk.candidates[0], "finish_reason", None) or finish_reason
            usage = getattr(chunk, "usage_metadata", None) or usage

    except errors.ClientError as exc:
        message = str(exc)
        if "API_KEY" in message.upper() or getattr(exc, "code", None) == 401:
            yield {"type": "error", "message": "GOOGLE_API_KEY was rejected. Check the key in .env."}
        elif getattr(exc, "code", None) == 429:
            yield {"type": "error", "message": "Gemini free-tier rate limit hit. Wait a minute and try again."}
        elif getattr(exc, "code", None) == 404:
            # Google retires models for new keys and the 404 body names the
            # replacement, so pass that through rather than a generic message.
            detail = ""
            if "'message':" in message:
                detail = message.split("'message':", 1)[1].split("', '", 1)[0].strip(" '\"")
            yield {"type": "error", "message": (
                f"Model '{settings.gemini_model}' is not available on this key. "
                f"{detail} Set GEMINI_MODEL in .env."
            )}
        else:
            logger.exception("Gemini client error")
            yield {"type": "error", "message": f"Gemini rejected the request: {message[:300]}"}
        return
    except errors.ServerError:
        logger.exception("Gemini server error")
        yield {"type": "error", "message": "Gemini is having trouble - try again shortly."}
        return
    except Exception as exc:
        logger.exception("Gemini request failed")
        yield {"type": "error", "message": f"Could not reach Gemini: {exc}"}
        return

    reason = str(finish_reason or "")
    if "SAFETY" in reason or "BLOCK" in reason or "PROHIBITED" in reason:
        logger.info("Gemini declined the request (finish_reason=%s)", reason)
        yield {"type": "text", "text": _REFUSAL_MESSAGE}

    yield {
        "type": "done",
        "stop_reason": reason or "stop",
        "usage": {
            "input_tokens": getattr(usage, "prompt_token_count", 0) or 0,
            "output_tokens": getattr(usage, "candidates_token_count", 0) or 0,
            "cache_read_input_tokens": getattr(usage, "cached_content_token_count", 0) or 0,
            "cache_creation_input_tokens": 0,
        },
    }


async def _gemini_structured(
    system: str, messages: list[dict[str, Any]], schema: dict[str, Any], max_tokens: int
) -> dict[str, Any]:
    client = _get_gemini()
    settings = get_settings()
    response = await client.aio.models.generate_content(
        model=settings.gemini_model,
        contents=_gemini_contents(messages),
        config=_gemini_config(system, max_tokens=max_tokens, schema=schema),
    )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError(
            "Gemini returned no content. This usually means the response was blocked "
            "or the token limit was hit before any JSON was produced."
        )
    return json.loads(text)


# ================================================================== Anthropic

_anthropic_client = None
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_fallbacks_enabled: bool | None = None


def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic

        settings = get_settings()
        if not settings.anthropic_api_key:
            raise LLMNotConfigured(_missing_key_message())
        _anthropic_client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key, timeout=120.0
        )
    return _anthropic_client


def _use_fallbacks() -> bool:
    global _fallbacks_enabled
    if _fallbacks_enabled is None:
        _fallbacks_enabled = get_settings().anthropic_server_fallbacks
    return _fallbacks_enabled


def _anthropic_kwargs(system: str, messages: list[dict[str, Any]], effort: str | None):
    settings = get_settings()
    return {
        "model": settings.anthropic_model,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": messages,
        "thinking": {"type": "adaptive", "display": settings.anthropic_thinking_display},
        "output_config": {"effort": effort or settings.anthropic_effort},
    }


async def _anthropic_stream(
    system: str, messages: list[dict[str, Any]], effort: str | None, max_tokens: int
) -> AsyncIterator[dict[str, Any]]:
    global _fallbacks_enabled
    import anthropic

    client = _get_anthropic()
    kwargs = _anthropic_kwargs(system, messages, effort) | {"max_tokens": max_tokens}

    for attempt in (1, 2):
        use_fallbacks = _use_fallbacks()
        try:
            if use_fallbacks:
                ctx = client.beta.messages.stream(**kwargs, betas=[_FALLBACK_BETA], fallbacks="default")
            else:
                ctx = client.messages.stream(**kwargs)

            async with ctx as stream:
                async for event in stream:
                    if event.type != "content_block_delta":
                        continue
                    if event.delta.type == "thinking_delta":
                        yield {"type": "thinking", "text": event.delta.thinking}
                    elif event.delta.type == "text_delta":
                        yield {"type": "text", "text": event.delta.text}
                final = await stream.get_final_message()

            if final.stop_reason == "refusal":
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
            text = str(getattr(exc, "message", "") or exc).lower()
            if attempt == 1 and use_fallbacks and ("fallback" in text or "beta" in text):
                _fallbacks_enabled = False
                continue
            yield {"type": "error", "message": f"Request rejected: {exc.message}"}
            return
        except anthropic.AuthenticationError:
            yield {"type": "error", "message": "ANTHROPIC_API_KEY was rejected. Check the key in .env."}
            return
        except anthropic.RateLimitError:
            yield {"type": "error", "message": "Rate limited by the Anthropic API. Try again shortly."}
            return
        except anthropic.APIStatusError as exc:
            logger.exception("Anthropic API error")
            yield {"type": "error", "message": f"API error ({exc.status_code})."}
            return
        except anthropic.APIConnectionError:
            yield {"type": "error", "message": "Could not reach the Anthropic API."}
            return


async def _anthropic_structured(
    system: str, messages: list[dict[str, Any]], schema: dict[str, Any],
    effort: str | None, max_tokens: int,
) -> dict[str, Any]:
    client = _get_anthropic()
    kwargs = _anthropic_kwargs(system, messages, effort) | {"max_tokens": max_tokens}
    kwargs["output_config"]["format"] = {"type": "json_schema", "schema": schema}

    if _use_fallbacks():
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


# =================================================================== dispatch

async def stream_answer(
    system: str,
    messages: list[dict[str, Any]],
    *,
    effort: str | None = None,
    max_tokens: int = 32000,
) -> AsyncIterator[dict[str, Any]]:
    """Yield ``{"type": ...}`` events: ``thinking``, ``text``, ``done``, ``error``."""
    if provider() == "gemini":
        async for event in _gemini_stream(system, messages, max_tokens):
            yield event
    else:
        async for event in _anthropic_stream(system, messages, effort, max_tokens):
            yield event


async def structured(
    system: str,
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
    *,
    effort: str | None = None,
    max_tokens: int = 16000,
) -> dict[str, Any]:
    """One call that must return JSON matching ``schema``."""
    if provider() == "gemini":
        return await _gemini_structured(system, messages, schema, max_tokens)
    return await _anthropic_structured(system, messages, schema, effort, max_tokens)
