from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import quote

from starlette.responses import Response, StreamingResponse

from .compat import ensure_agent_framework_compat

ensure_agent_framework_compat()

from agent_framework import Content, Message
from agent_framework_foundry_hosting import ResponsesHostServer
from agent_framework_foundry_hosting._responses import (
    ResponseEventStream,
    _items_to_messages,
    _OutputItemTracker,
    _to_outputs,
)
from azure.ai.agentserver.responses.streaming._sse import encode_sse_any_event
from azure.ai.agentserver.responses.store._memory import InMemoryResponseProvider

logger = logging.getLogger(__name__)

DEFAULT_LIVE_VIEW_BASE_URL = "https://pwwdashboard-f4gkeyekh5bucqb3.eastus-01.azurewebsites.net/"


class DirectStreamingResponsesMiddleware:
    """Handle POST /responses directly with Agent Framework streaming updates."""

    def __init__(self, app: Any, host: "StreamingResponsesHostServer") -> None:
        self._app = app
        self._host = host

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") != "/responses":
            await self._app(scope, receive, send)
            return

        body = await _read_body(receive)
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError:
            await Response("Invalid JSON request body.", status_code=400)(scope, receive, send)
            return

        if not isinstance(payload, dict):
            await Response("Responses request body must be a JSON object.", status_code=400)(scope, receive, send)
            return

        response = StreamingResponse(
            self._host.stream_response(payload),
            media_type="text/event-stream",
            headers={"connection": "keep-alive", "cache-control": "no-cache", "x-accel-buffering": "no"},
        )
        await response(scope, receive, send)


class StreamingResponsesHostServer(ResponsesHostServer):
    """Responses host with a direct Agent Framework streaming create-response path."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("store", InMemoryResponseProvider())
        super().__init__(*args, **kwargs)
        self.add_middleware(DirectStreamingResponsesMiddleware, host=self)

    async def stream_response(self, payload: dict[str, Any]) -> AsyncIterator[str]:
        response_id = payload.get("id") or f"resp_{uuid.uuid4().hex}"
        logger.info("Streaming mode handling response %s directly from request input.", response_id)
        run_kwargs: dict[str, Any] = {"messages": _input_to_messages(payload.get("input"))}

        response_event_stream = ResponseEventStream(response_id=response_id, model=payload.get("model"))

        yield encode_sse_any_event(response_event_stream.emit_created())
        yield encode_sse_any_event(response_event_stream.emit_in_progress())

        tracker = _OutputItemTracker(response_event_stream)
        emitted_browser_session_urls: set[str] = set()
        emitted_create_session_progress = False
        create_session_call_ids: set[str] = set()
        logger.info("Starting Agent Framework streaming run for response %s.", response_id)
        async for update in self._agent.run(stream=True, **run_kwargs):
            for content in update.contents:
                if _is_create_session_call(content) and not emitted_create_session_progress:
                    emitted_create_session_progress = True
                    call_id = getattr(content, "call_id", None)
                    if isinstance(call_id, str) and call_id:
                        create_session_call_ids.add(call_id)
                    async for event in response_event_stream.aoutput_item_message(
                        "Creating browser session...\n\n"
                    ):
                        yield encode_sse_any_event(event)
                for event in tracker.handle(content):
                    yield encode_sse_any_event(event)
                browser_session = _extract_browser_session(content, create_session_call_ids=create_session_call_ids)
                if browser_session and browser_session.live_view_url not in emitted_browser_session_urls:
                    emitted_browser_session_urls.add(browser_session.live_view_url)
                    async for event in response_event_stream.aoutput_item_message(
                        f"Created a new browser session {browser_session.live_view_url}\n\n"
                    ):
                        yield encode_sse_any_event(event)
                if tracker.needs_async:
                    async for item in _to_outputs(response_event_stream, content):
                        yield encode_sse_any_event(item)
                    tracker.needs_async = False

        for event in tracker.close():
            yield encode_sse_any_event(event)

        yield encode_sse_any_event(response_event_stream.emit_completed())


async def _read_body(receive: Any) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return b"".join(chunks)
        if message["type"] != "http.request":
            continue
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            return b"".join(chunks)


def _input_to_messages(input_value: Any) -> Any:
    if isinstance(input_value, str):
        return input_value
    input_items = [input_value] if isinstance(input_value, dict) else input_value or []
    if not any(isinstance(item, dict) for item in input_items):
        return _items_to_messages(input_items)
    return [
        _dict_item_to_message(item) if isinstance(item, dict) else _items_to_messages([item])[0]
        for item in input_items
    ]


def _dict_item_to_message(item: dict[str, Any]) -> Message:
    item_type = item.get("type")
    if item_type in {None, "message", "input_message"}:
        return Message(
            role=item.get("role") or "user",
            contents=_dict_content_to_contents(item.get("content")),
        )
    if item_type == "function_call":
        return Message(
            role="assistant",
            contents=[
                Content.from_function_call(
                    item.get("call_id") or item.get("id") or "",
                    item.get("name") or "",
                    arguments=item.get("arguments") or item.get("input") or "",
                )
            ],
        )
    if item_type in {"function_call_output", "custom_tool_call_output"}:
        output = item.get("output")
        return Message(
            role="tool",
            contents=[
                Content.from_function_result(
                    item.get("call_id") or "",
                    result=output if isinstance(output, str) else str(output),
                )
            ],
        )
    return Message(role=item.get("role") or "user", contents=[Content.from_text(json.dumps(item))])


def _dict_content_to_contents(content: Any) -> list[Content]:
    if isinstance(content, str):
        return [Content.from_text(content)]
    if not isinstance(content, list):
        return [Content.from_text("" if content is None else str(content))]

    contents: list[Content] = []
    for part in content:
        if isinstance(part, str):
            contents.append(Content.from_text(part))
            continue
        if not isinstance(part, dict):
            contents.append(Content.from_text(str(part)))
            continue
        text = part.get("text") or part.get("content")
        if text is not None:
            contents.append(Content.from_text(str(text)))
    return contents or [Content.from_text("")]


class BrowserSessionInfo:
    def __init__(self, *, cdp_url: str | None, live_view_url: str) -> None:
        self.cdp_url = cdp_url
        self.live_view_url = live_view_url


def _is_create_session_call(content: Any) -> bool:
    content_type = getattr(content, "type", None)
    tool_name = getattr(content, "tool_name", None) or getattr(content, "name", None) or ""
    return content_type in {"mcp_server_tool_call", "function_call"} and _is_create_session_tool_name(tool_name)


def _extract_browser_session(content: Any, *, create_session_call_ids: set[str] | None = None) -> BrowserSessionInfo | None:
    content_type = getattr(content, "type", None)
    if content_type == "mcp_server_tool_result":
        output = getattr(content, "output", None)
    elif content_type == "function_result":
        call_id = getattr(content, "call_id", None)
        if create_session_call_ids is not None and call_id not in create_session_call_ids:
            return None
        output = getattr(content, "result", None)
    else:
        return None
    live_view_url = _find_url_value(
        output,
        preferred_keys=("liveViewUrl", "live_view_url", "live_view"),
        allow_generic_url=False,
    )
    cdp_url = _find_url_value(output, preferred_keys=("cdpUrl", "cdp_url"), allow_generic_url=True)
    if live_view_url:
        return BrowserSessionInfo(cdp_url=cdp_url, live_view_url=live_view_url)
    if cdp_url:
        return BrowserSessionInfo(cdp_url=cdp_url, live_view_url=_build_live_view_url(cdp_url))
    return None


def _is_create_session_tool_name(tool_name: str) -> bool:
    normalized = tool_name.lower().replace("-", "_")
    return normalized == "create_session" or normalized.endswith("___create_session")


def _build_live_view_url(cdp_url: str) -> str:
    live_view_cdp = cdp_url + ("&" if "?" in cdp_url else "?") + "isSecondaryConnection=true"
    base_url = os.getenv("BROWSER_AGENT_LIVE_VIEW_BASE_URL", DEFAULT_LIVE_VIEW_BASE_URL).rstrip("/")
    return f"{base_url}/?cdp={quote(live_view_cdp, safe='')}"


def _find_url_value(value: Any, *, preferred_keys: tuple[str, ...], allow_generic_url: bool) -> str | None:
    if isinstance(value, Mapping):
        for key in preferred_keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for nested in value.values():
            candidate = _find_url_value(
                nested,
                preferred_keys=preferred_keys,
                allow_generic_url=allow_generic_url,
            )
            if candidate:
                return candidate
        return None

    if hasattr(value, "model_dump"):
        try:
            return _find_url_value(
                value.model_dump(),
                preferred_keys=preferred_keys,
                allow_generic_url=allow_generic_url,
            )
        except (TypeError, ValueError):
            pass

    if hasattr(value, "__dict__") and not isinstance(value, type):
        candidate = _find_url_value(vars(value), preferred_keys=preferred_keys, allow_generic_url=allow_generic_url)
        if candidate:
            return candidate

    if isinstance(value, list):
        for item in value:
            candidate = _find_url_value(item, preferred_keys=preferred_keys, allow_generic_url=allow_generic_url)
            if candidate:
                return candidate
        return None

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return _find_url_value(
                json.loads(stripped),
                preferred_keys=preferred_keys,
                allow_generic_url=allow_generic_url,
            )
        except json.JSONDecodeError:
            pass
        for key in preferred_keys:
            match = re.search(rf'"?{re.escape(key)}"?\s*[:=]\s*"([^"]+)"', stripped)
            if match:
                return match.group(1).strip()
        if not allow_generic_url:
            return None
        match = re.search(r"\b(?:wss|https)://[^\s\"'<>]+", stripped)
        return match.group(0) if match else None

    return None
