from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import sys
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any

from .compat import ensure_agent_framework_compat

ensure_agent_framework_compat()

from agent_framework._mcp import MCPStdioTool
from agent_framework._tools import tool
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import Field

from .logging import log_yellow, redact_sensitive_values
from .paths import project_root
from .settings import AgentSettings


def make_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    environment_scripts_path = Path(sys.executable).parent
    env["PATH"] = str(environment_scripts_path) + os.pathsep + env.get("PATH", "")
    return env


def resolve_playwright_cli_command(env: dict[str, str]) -> str:
    return shutil.which("playwright-cli", path=env["PATH"]) or "playwright-cli"


def decode_subprocess_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


async def run_playwright_cli_cleanup_command(
    playwright_cli: str,
    session_id: str | None,
    command: str,
    timeout_seconds: int,
    env: dict[str, str],
) -> dict[str, Any]:
    args = [playwright_cli]
    if session_id:
        args.append(f"-s={session_id}")
    args.append(command)

    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=str(project_root()),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
        timed_out = False
    except asyncio.TimeoutError:
        process.kill()
        stdout, stderr = await process.communicate()
        timed_out = True

    return {
        "command": redact_sensitive_values(" ".join(args)),
        "timedOut": timed_out,
        "exitCode": process.returncode,
        "stdout": redact_sensitive_values(decode_subprocess_output(stdout)).strip(),
        "stderr": redact_sensitive_values(decode_subprocess_output(stderr)).strip(),
    }


def parse_playwright_cli_command(command: str) -> list[str]:
    try:
        parts = shlex.split(command, posix=True)
    except ValueError as ex:
        raise ValueError(f"Invalid playwright-cli command arguments: {ex}") from ex
    if not parts:
        raise ValueError("command is required.")
    if parts[0] in {"playwright-cli", "npx", "npm"}:
        raise ValueError("Pass only playwright-cli arguments, not the executable name.")
    return parts


def make_run_playwright_cli(settings: AgentSettings):
    @tool(
        name="run_playwright_cli",
        description=(
            "Run playwright-cli with a named session and return stdout, stderr, and exit code. "
            "Set cdpUrl on the first command after create_browser_session so the tool can pass "
            "PLAYWRIGHT_MCP_CDP_ENDPOINT to playwright-cli."
        ),
    )
    async def run_playwright_cli(
        sessionId: Annotated[str, Field(description="Browser session id previously passed to create_browser_session.")],
        command: Annotated[
            str,
            Field(description='playwright-cli arguments, excluding the executable and session. Example: "goto https://example.com".'),
        ],
        cdpUrl: Annotated[
            str | None,
            Field(description="CDP WebSocket URL returned by create_browser_session. Pass only for the first open/attach command."),
        ] = None,
        timeout_seconds: Annotated[
            int | None,
            Field(description="Optional timeout in seconds. Defaults to the agent Playwright CLI timeout."),
        ] = None,
    ) -> str:
        session_id = sessionId.strip()
        if not session_id:
            raise ValueError("sessionId is required.")

        env = make_subprocess_env()
        if cdpUrl:
            env["PLAYWRIGHT_MCP_CDP_ENDPOINT"] = cdpUrl

        effective_timeout = timeout_seconds or settings.playwright_cli_timeout_seconds
        playwright_cli = resolve_playwright_cli_command(env)
        cli_args = parse_playwright_cli_command(command)
        process_args = [playwright_cli, f"-s={session_id}", *cli_args]
        safe_command = redact_sensitive_values(" ".join(process_args))
        log_yellow(f"[run_playwright_cli] timeout={effective_timeout}s command={safe_command}")

        process = await asyncio.create_subprocess_exec(
            *process_args,
            cwd=str(project_root()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
            stdout = redact_sensitive_values(decode_subprocess_output(stdout_bytes))
            stderr = redact_sensitive_values(decode_subprocess_output(stderr_bytes))
            return (
                f"Command timed out after {effective_timeout} seconds.\n"
                f"stdout:\n{stdout}\n\nstderr:\n{stderr}"
            )

        stdout = redact_sensitive_values(decode_subprocess_output(stdout_bytes))
        stderr = redact_sensitive_values(decode_subprocess_output(stderr_bytes))
        return (
            f"exit_code: {process.returncode}\n"
            f"stdout:\n{stdout or '<empty>'}\n\n"
            f"stderr:\n{stderr or '<empty>'}"
        )

    return run_playwright_cli


async def call_mcp_end_browser_session(settings: AgentSettings, session_id: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["AZURE_PLAYWRIGHT_SERVICE_URL"] = settings.service_url
    env["AZURE_PLAYWRIGHT_SERVICE_ACCESS_TOKEN"] = settings.access_token

    server = StdioServerParameters(
        command="node",
        args=[str(settings.mcp_server_path)],
        env=env,
        cwd=str(settings.mcp_server_path.parent),
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=settings.mcp_timeout_seconds),
        ) as session:
            await session.initialize()
            result = await session.call_tool("end_browser_session", {"sessionId": session_id})

    text_parts = [getattr(item, "text", "") for item in result.content if getattr(item, "text", None)]
    text = "\n".join(text_parts)
    if result.isError:
        raise RuntimeError(text or "MCP end_browser_session returned an error.")
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


def make_close_browser_session(settings: AgentSettings):
    @tool(
        name="close_browser_session",
        description=(
            "Close a browser automation session. This first runs playwright-cli detach "
            "to release local Playwright CLI state, then calls the Playwright Service MCP end_browser_session tool."
        ),
    )
    async def close_browser_session(
        sessionId: Annotated[
            str,
            Field(description="The browser session id previously passed to create_browser_session."),
        ],
    ) -> str:
        session_id = sessionId.strip()
        if not session_id:
            raise ValueError("sessionId is required.")

        env = make_subprocess_env()
        playwright_cli = resolve_playwright_cli_command(env)
        log_yellow(f"[playwright-cli] detach sessionId={session_id}")
        detach_result = await run_playwright_cli_cleanup_command(
            playwright_cli,
            session_id,
            "detach",
            settings.playwright_cli_timeout_seconds,
            env,
        )

        log_yellow(f"[MCP] end_browser_session arguments={{'sessionId': '{session_id}'}}")
        try:
            mcp_result = await call_mcp_end_browser_session(settings, session_id)
            mcp_end_error = None
        except Exception as ex:
            mcp_result = {}
            mcp_end_error = redact_sensitive_values(str(ex))

        result = json.dumps(
            {
                "sessionId": session_id,
                "playwrightCliDetach": detach_result,
                "mcpEndResult": mcp_result,
                "mcpEndError": mcp_end_error,
            },
            indent=2,
        )
        return redact_sensitive_values(result)

    return close_browser_session


def make_mcp_tool(settings: AgentSettings) -> MCPStdioTool:
    env = os.environ.copy()
    env["AZURE_PLAYWRIGHT_SERVICE_URL"] = settings.service_url
    env["AZURE_PLAYWRIGHT_SERVICE_ACCESS_TOKEN"] = settings.access_token

    return MCPStdioTool(
        name="azure-playwright-service",
        command="node",
        args=[str(settings.mcp_server_path)],
        env=env,
        request_timeout=settings.mcp_timeout_seconds,
        allowed_tools={"create_browser_session"},
        load_prompts=False,
        description="Creates Azure Playwright Service remote browser sessions.",
    )

