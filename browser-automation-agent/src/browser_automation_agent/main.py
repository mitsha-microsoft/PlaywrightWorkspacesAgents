from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import shutil
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from bu_agent_sdk import Agent
from bu_agent_sdk.agent import (
    FinalResponseEvent,
    TaskComplete,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from bu_agent_sdk.llm import ChatAnthropic, ChatOpenAI
from bu_agent_sdk.llm.base import ToolChoice, ToolDefinition
from bu_agent_sdk.llm.messages import (
    AssistantMessage,
    BaseMessage,
    DeveloperMessage,
    Function,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from bu_agent_sdk.llm.views import ChatInvokeCompletion, ChatInvokeUsage
from bu_agent_sdk.tools import Depends, tool
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI

BLUE = "\033[94m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"


@dataclass
class AgentSettings:
    provider: str
    model: str
    azure_openai_endpoint: str | None
    azure_openai_scope: str
    service_url: str | None
    access_token: str | None
    mcp_server_path: Path
    browser_use_command: list[str]
    verbose: bool
    mcp_timeout_seconds: int


@dataclass
class ActiveBrowserSession:
    session_id: str
    cdp_url: str


@dataclass
class AgentRuntime:
    settings: AgentSettings
    active_session: ActiveBrowserSession | None = None


def get_runtime() -> AgentRuntime:
    raise RuntimeError("Agent runtime dependency was not configured.")


def color(message: str, code: str) -> str:
    return f"{code}{message}{RESET}"


def log_mcp_create(session_id: str) -> None:
    print(color(f"[MCP] create_browser_session sessionId={session_id}", BLUE), file=sys.stderr)


def log_mcp_end(session_id: str) -> None:
    print(color(f"[MCP] end_browser_session sessionId={session_id}", YELLOW), file=sys.stderr)


def log_verbose(runtime: AgentRuntime, message: str) -> None:
    if runtime.settings.verbose:
        print(color(f"[verbose] {message}", DIM), file=sys.stderr)


def default_mcp_server_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "azure-playwright-service-mcp"
        / "src"
        / "index.js"
    )


def load_browser_use_skill() -> str:
    skill_path = Path(__file__).with_name("browser_use_skill.md")
    return skill_path.read_text(encoding="utf-8")


def default_browser_use_command() -> list[str]:
    executable_name = "browser-use.exe" if os.name == "nt" else "browser-use"
    environment_executable = Path(sys.executable).with_name(executable_name)
    if environment_executable.exists():
        return [str(environment_executable)]

    path_executable = shutil.which("browser-use")
    if path_executable:
        return [path_executable]

    return ["browser-use"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Long-running browser automation agent backed by Azure Playwright Service MCP.",
    )
    parser.add_argument(
        "--provider",
        choices=("openai", "anthropic", "azure-openai"),
        default=os.getenv("BROWSER_AGENT_LLM_PROVIDER")
        or ("azure-openai" if os.getenv("AZURE_OPENAI_ENDPOINT") else "openai"),
        help=(
            "LLM provider. Defaults to BROWSER_AGENT_LLM_PROVIDER, "
            "or azure-openai when AZURE_OPENAI_ENDPOINT is set, otherwise openai."
        ),
    )
    parser.add_argument(
        "--model",
        default=os.getenv("BROWSER_AGENT_MODEL"),
        help=(
            "Model name. For azure-openai, this is the Azure OpenAI deployment name. "
            "Defaults to gpt-4o for OpenAI, AZURE_OPENAI_DEPLOYMENT_NAME for Azure OpenAI, "
            "or claude-sonnet-4-20250514 for Anthropic."
        ),
    )
    parser.add_argument(
        "--azure-openai-endpoint",
        default=os.getenv("AZURE_OPENAI_ENDPOINT"),
        help="Azure OpenAI endpoint, for example https://my-resource.openai.azure.com.",
    )
    parser.add_argument(
        "--azure-openai-scope",
        default=os.getenv(
            "AZURE_OPENAI_SCOPE",
            "https://ai.azure.com/.default",
        ),
        help="AAD token scope for Azure OpenAI.",
    )
    parser.add_argument(
        "--service-url",
        default=os.getenv("AZURE_PLAYWRIGHT_SERVICE_URL") or os.getenv("PLAYWRIGHT_SERVICE_URL"),
        help="Playwright Service WSS endpoint. Prefer environment variables for repeated use.",
    )
    parser.add_argument(
        "--access-token",
        default=os.getenv("AZURE_PLAYWRIGHT_SERVICE_ACCESS_TOKEN")
        or os.getenv("PLAYWRIGHT_SERVICE_ACCESS_TOKEN"),
        help="Playwright Service access token. Prefer environment variables or secret management.",
    )
    parser.add_argument(
        "--mcp-server-path",
        type=Path,
        default=default_mcp_server_path(),
        help="Path to azure-playwright-service-mcp/src/index.js.",
    )
    parser.add_argument(
        "--browser-use-command",
        default=os.getenv("BROWSER_USE_COMMAND"),
        help=(
            "browser-use executable command. Defaults to the browser-use executable "
            "from the current Python environment."
        ),
    )
    parser.add_argument(
        "--mcp-timeout-seconds",
        type=int,
        default=int(os.getenv("BROWSER_AGENT_MCP_TIMEOUT_SECONDS", "120")),
        help="Timeout for MCP tool calls.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=os.getenv("BROWSER_AGENT_VERBOSE", "").lower() in {"1", "true", "yes"},
        help="Print Browser Use CLI commands and agent tool calls.",
    )
    return parser.parse_args()


def make_settings(args: argparse.Namespace) -> AgentSettings:
    provider = args.provider.lower()
    model = args.model
    if not model:
        if provider == "anthropic":
            model = "claude-sonnet-4-20250514"
        elif provider == "azure-openai":
            model = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME") or "gpt-4o"
        else:
            model = "gpt-4o"

    browser_use_command = (
        shlex.split(args.browser_use_command)
        if args.browser_use_command
        else default_browser_use_command()
    )

    return AgentSettings(
        provider=provider,
        model=model,
        azure_openai_endpoint=args.azure_openai_endpoint,
        azure_openai_scope=args.azure_openai_scope,
        service_url=args.service_url,
        access_token=args.access_token,
        mcp_server_path=args.mcp_server_path.resolve(),
        browser_use_command=browser_use_command,
        verbose=args.verbose,
        mcp_timeout_seconds=args.mcp_timeout_seconds,
    )


@dataclass
class ChatAzureOpenAIEntra(ChatOpenAI):
    azure_endpoint: str = ""
    azure_scope: str = "https://ai.azure.com/.default"

    def get_client(self) -> AsyncOpenAI:
        credential = DefaultAzureCredential()
        token_provider = get_bearer_token_provider(credential, self.azure_scope)

        async def async_token_provider() -> str:
            return token_provider()

        client_params = {
            "base_url": self.azure_endpoint,
            "api_key": async_token_provider,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "default_headers": self.default_headers,
            "default_query": self.default_query,
            "_strict_response_validation": self._strict_response_validation,
        }
        if self.http_client is not None:
            client_params["http_client"] = self.http_client
        return AsyncOpenAI(
            **{key: value for key, value in client_params.items() if value is not None},
        )

    @staticmethod
    def _message_text(message: Any) -> str:
        if getattr(message, "destroyed", False):
            return "<removed to save context>"
        text = getattr(message, "text", None)
        if isinstance(text, str):
            return text
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                getattr(part, "text", "[non-text content]") for part in content
            )
        return str(content or "")

    def _serialize_responses_input(self, messages: list[BaseMessage]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, UserMessage):
                items.append(
                    {
                        "type": "message",
                        "role": "user",
                        "content": self._message_text(message),
                    },
                )
            elif isinstance(message, SystemMessage):
                items.append(
                    {
                        "type": "message",
                        "role": "system",
                        "content": self._message_text(message),
                    },
                )
            elif isinstance(message, DeveloperMessage):
                items.append(
                    {
                        "type": "message",
                        "role": "developer",
                        "content": self._message_text(message),
                    },
                )
            elif isinstance(message, AssistantMessage):
                content = self._message_text(message)
                if content:
                    items.append(
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": content,
                        },
                    )
                for tool_call in message.tool_calls or []:
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": tool_call.id,
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    )
            elif isinstance(message, ToolMessage):
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": self._message_text(message),
                    },
                )
            else:
                raise ValueError(f"Unknown message type: {type(message)}")
        return items

    def _serialize_responses_tools(
        self,
        tools: list[ToolDefinition] | None,
    ) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "strict": tool.strict,
            }
            for tool in tools
        ]

    @staticmethod
    def _responses_tool_choice(tool_choice: ToolChoice | None) -> Any:
        if tool_choice in (None, "auto", "required", "none"):
            return tool_choice
        return {"type": "function", "name": tool_choice}

    @staticmethod
    def _responses_usage(response: Any) -> ChatInvokeUsage | None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        usage_dump = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
        input_tokens = usage_dump.get("input_tokens", 0) or 0
        output_tokens = usage_dump.get("output_tokens", 0) or 0
        total_tokens = usage_dump.get("total_tokens", input_tokens + output_tokens)
        input_details = usage_dump.get("input_tokens_details") or {}
        return ChatInvokeUsage(
            prompt_tokens=input_tokens,
            prompt_cached_tokens=input_details.get("cached_tokens"),
            prompt_cache_creation_tokens=None,
            prompt_image_tokens=None,
            completion_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    @staticmethod
    def _extract_responses_text_and_tools(response: Any) -> tuple[str | None, list[ToolCall]]:
        response_dump = response.model_dump() if hasattr(response, "model_dump") else {}
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text:
            content_parts.append(output_text)

        for item in response_dump.get("output", []) or []:
            item_type = item.get("type")
            if item_type == "function_call":
                call_id = item.get("call_id") or item.get("id")
                name = item.get("name")
                arguments = item.get("arguments") or "{}"
                if call_id and name:
                    tool_calls.append(
                        ToolCall(
                            id=call_id,
                            function=Function(name=name, arguments=arguments),
                            type="function",
                        ),
                    )
            elif item_type == "message":
                for part in item.get("content", []) or []:
                    if part.get("type") in {"output_text", "text"} and part.get("text"):
                        text = part["text"]
                        if text not in content_parts:
                            content_parts.append(text)

        return ("\n".join(content_parts) if content_parts else None), tool_calls

    async def ainvoke(
        self,
        messages: list[BaseMessage],
        tools: list[ToolDefinition] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion:
        model_params: dict[str, Any] = {
            "model": self.model,
            "input": self._serialize_responses_input(messages),
        }

        if self.temperature is not None:
            model_params["temperature"] = self.temperature
        if self.max_completion_tokens is not None:
            model_params["max_output_tokens"] = self.max_completion_tokens
        if self.top_p is not None:
            model_params["top_p"] = self.top_p
        if self.service_tier is not None:
            model_params["service_tier"] = self.service_tier
        serialized_tools = self._serialize_responses_tools(tools)
        if serialized_tools:
            model_params["tools"] = serialized_tools
            model_params["parallel_tool_calls"] = self.parallel_tool_calls
            responses_tool_choice = self._responses_tool_choice(tool_choice)
            if responses_tool_choice is not None:
                model_params["tool_choice"] = responses_tool_choice

        response = await self.get_client().responses.create(**model_params)
        content, tool_calls = self._extract_responses_text_and_tools(response)

        return ChatInvokeCompletion(
            content=content,
            tool_calls=tool_calls,
            usage=self._responses_usage(response),
            stop_reason=getattr(response, "status", None),
        )


def make_llm(settings: AgentSettings) -> Any:
    if settings.provider == "anthropic":
        return ChatAnthropic(model=settings.model)
    if settings.provider == "azure-openai":
        if not settings.azure_openai_endpoint:
            raise ValueError(
                "Azure OpenAI endpoint is required. Pass --azure-openai-endpoint or set AZURE_OPENAI_ENDPOINT.",
            )
        return ChatAzureOpenAIEntra(
            model=settings.model,
            azure_endpoint=settings.azure_openai_endpoint,
            azure_scope=settings.azure_openai_scope,
            temperature=None,
            frequency_penalty=None,
            max_completion_tokens=None,
            prompt_cache_key=None,
            prompt_cache_retention=None,
        )
    if settings.provider == "openai":
        return ChatOpenAI(model=settings.model)
    raise ValueError(f"Unsupported provider: {settings.provider}")


@asynccontextmanager
async def mcp_session(runtime: AgentRuntime):
    settings = runtime.settings
    env = os.environ.copy()
    if settings.service_url:
        env["AZURE_PLAYWRIGHT_SERVICE_URL"] = settings.service_url
    if settings.access_token:
        env["AZURE_PLAYWRIGHT_SERVICE_ACCESS_TOKEN"] = settings.access_token

    server = StdioServerParameters(
        command="node",
        args=[str(settings.mcp_server_path)],
        env=env,
        cwd=settings.mcp_server_path.parent,
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=settings.mcp_timeout_seconds),
        ) as session:
            await session.initialize()
            yield session


def parse_mcp_json_result(result: Any) -> dict[str, Any]:
    if result.isError:
        texts = [getattr(item, "text", "") for item in result.content]
        raise RuntimeError("\n".join(texts) or "MCP tool returned an error.")

    for item in result.content:
        text = getattr(item, "text", None)
        if text:
            return json.loads(text)

    raise RuntimeError("MCP tool returned no text content.")


async def call_mcp_tool(runtime: AgentRuntime, tool_name: str, session_id: str) -> dict[str, Any]:
    async with mcp_session(runtime) as session:
        result = await session.call_tool(tool_name, {"sessionId": session_id})
        return parse_mcp_json_result(result)


async def run_browser_use(
    runtime: AgentRuntime,
    args: list[str],
    *,
    include_cdp_url: str | None = None,
) -> str:
    if not runtime.active_session:
        raise RuntimeError("No active browser session. Call create_remote_browser_session first.")

    command = [
        *runtime.settings.browser_use_command,
        "--session",
        runtime.active_session.session_id,
    ]
    if include_cdp_url:
        command.extend(["--cdp-url", include_cdp_url])
    command.extend(args)

    display_command = [
        "<cdp-url>" if include_cdp_url and part == include_cdp_url else part
        for part in command
    ]
    log_verbose(runtime, " ".join(shlex.quote(part) for part in display_command))

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    stdout_text = stdout.decode(errors="replace")
    stderr_text = stderr.decode(errors="replace")
    output = (stdout_text + stderr_text).strip()

    if process.returncode != 0:
        if include_cdp_url and "unrecognized arguments: --cdp-url" in output:
            raise RuntimeError(
                "The installed browser-use CLI does not support --cdp-url. "
                "Upgrade browser-use to a version that supports connecting to an existing CDP WebSocket URL.",
            )
        raise RuntimeError(output or f"browser-use exited with code {process.returncode}.")

    return output or "(no output)"


@tool(
    "Create a remote browser session through the Azure Playwright Service MCP server, then attach browser-use to it and keep it alive.",
)
async def create_remote_browser_session(
    session_id: str,
    runtime: Annotated[AgentRuntime, Depends(get_runtime)],
) -> str:
    if runtime.active_session:
        return (
            f"A browser session is already active: {runtime.active_session.session_id}. "
            "Use close_remote_browser_session before creating another one."
        )

    normalized_session_id = session_id.strip() or f"browser-{uuid.uuid4().hex[:8]}"
    log_mcp_create(normalized_session_id)
    result = await call_mcp_tool(runtime, "create_browser_session", normalized_session_id)
    cdp_url = result.get("cdpUrl") or result.get("sessionUrl")
    if not isinstance(cdp_url, str) or not cdp_url.startswith("wss://"):
        raise RuntimeError(f"MCP create_browser_session returned an invalid CDP URL: {result}")

    runtime.active_session = ActiveBrowserSession(
        session_id=normalized_session_id,
        cdp_url=cdp_url,
    )

    try:
        attach_output = await run_browser_use(
            runtime,
            ["open", "about:blank"],
            include_cdp_url=cdp_url,
        )
    except Exception as attach_error:
        runtime.active_session = None
        log_mcp_end(normalized_session_id)
        try:
            await call_mcp_tool(runtime, "end_browser_session", normalized_session_id)
        except Exception as cleanup_error:
            raise RuntimeError(
                f"Failed to attach browser-use to the remote session: {attach_error}. "
                f"Also failed to clean up the MCP session: {cleanup_error}"
            ) from attach_error
        raise RuntimeError(
            f"Failed to attach browser-use to the remote session; MCP session was cleaned up: {attach_error}"
        ) from attach_error

    return json.dumps(
        {
            "sessionId": normalized_session_id,
            "cdpUrl": cdp_url,
            "browserUseSessionKeptAlive": True,
            "attachOutput": attach_output,
        },
        indent=2,
    )


@tool(
    "Run a browser-use CLI command in the active kept-alive browser session. Do not use this to close the session.",
    ephemeral=3,
)
async def browser_use(
    command: str,
    runtime: Annotated[AgentRuntime, Depends(get_runtime)],
) -> str:
    if not runtime.active_session:
        raise RuntimeError("No active browser session. Call create_remote_browser_session first.")

    stripped = command.strip()
    if not stripped:
        raise RuntimeError("browser-use command cannot be empty.")
    if stripped.startswith("browser-use"):
        raise RuntimeError("Pass only the browser-use subcommand, not the 'browser-use' executable name.")

    parts = shlex.split(stripped)
    if parts and parts[0] == "close":
        raise RuntimeError("Use close_remote_browser_session to close the kept-alive browser session.")

    return await run_browser_use(runtime, parts)


@tool("Close the active browser-use session, then call the Azure Playwright Service MCP end_browser_session tool.")
async def close_remote_browser_session(
    runtime: Annotated[AgentRuntime, Depends(get_runtime)],
) -> str:
    if not runtime.active_session:
        return "No active browser session to close."

    session_id = runtime.active_session.session_id
    close_output = ""
    close_error = None

    try:
        close_output = await run_browser_use(runtime, ["close"])
    except Exception as error:
        close_error = error

    log_mcp_end(session_id)
    mcp_result = await call_mcp_tool(runtime, "end_browser_session", session_id)
    runtime.active_session = None

    if close_error:
        raise RuntimeError(
            "MCP end_browser_session completed, but browser-use close failed first: "
            f"{close_error}"
        )

    return json.dumps(
        {
            "sessionId": session_id,
            "browserUseClosed": True,
            "browserUseOutput": close_output,
            "mcpEndResult": mcp_result,
        },
        indent=2,
    )


@tool("Signal that the current user request is complete.")
async def done(message: str) -> str:
    raise TaskComplete(message)


BASE_SYSTEM_PROMPT = """You are a browser automation command-line agent.

Use the tools to create and keep alive one remote browser session at a time:
1. Call create_remote_browser_session with a clear session_id before browser work.
2. Use browser_use for browser-use CLI subcommands such as:
   - open https://example.com
   - state
   - click 5
   - input 3 "text"
   - keys Enter
   - screenshot output.png
   - get title
   - eval "document.title"
3. Do not reconnect to the CDP URL after the session is created. The Browser Use daemon keeps the session alive.
4. When the user asks to finish, stop, close, clean up, or end the browser, call close_remote_browser_session.
5. Never use browser_use with the close command; always use close_remote_browser_session.
6. Use done when the current user turn is complete.

Be concise in final responses. If a browser-use command fails because --cdp-url is unsupported, tell the user to upgrade browser-use.
"""


async def run_repl() -> None:
    settings = make_settings(parse_args())
    runtime = AgentRuntime(settings=settings)

    if not settings.mcp_server_path.exists():
        raise FileNotFoundError(f"MCP server not found: {settings.mcp_server_path}")

    agent = Agent(
        llm=make_llm(settings),
        tools=[create_remote_browser_session, browser_use, close_remote_browser_session, done],
        system_prompt=f"{BASE_SYSTEM_PROMPT}\n\n{load_browser_use_skill()}",
        dependency_overrides={get_runtime: lambda: runtime},
        require_done_tool=True,
    )

    print("Browser automation agent ready. Type 'exit' or press Ctrl+C to quit.")
    if settings.verbose:
        print(color("Verbose logging enabled.", DIM), file=sys.stderr)

    while True:
        try:
            user_message = input("\nbrowser-agent> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_message:
            continue
        if user_message.lower() in {"exit", "quit"}:
            break

        final_response = None
        try:
            async for event in agent.query_stream(user_message):
                if isinstance(event, ToolCallEvent):
                    log_verbose(runtime, f"agent tool call: {event.tool} {event.args}")
                elif isinstance(event, ToolResultEvent):
                    log_verbose(runtime, f"agent tool result: {event.tool} error={event.is_error}")
                elif isinstance(event, TextEvent) and event.content:
                    print(event.content, end="", flush=True)
                elif isinstance(event, FinalResponseEvent):
                    final_response = event.content
        except Exception as error:
            print(f"Error: {error}", file=sys.stderr)
            continue

        if final_response:
            print(final_response)


def run() -> None:
    asyncio.run(run_repl())


if __name__ == "__main__":
    run()
