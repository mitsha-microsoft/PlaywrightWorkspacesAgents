from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import warnings
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any

import agent_framework as _agent_framework

if not hasattr(_agent_framework, "__version__"):
    _agent_framework.__version__ = importlib.metadata.version("agent-framework-core")

for _name, _module in {
    "Agent": "agent_framework._agents",
    "BaseAgent": "agent_framework._agents",
    "RawAgent": "agent_framework._agents",
    "SupportsAgentRun": "agent_framework._agents",
    "AgentMiddlewareLayer": "agent_framework._middleware",
    "AgentSession": "agent_framework._sessions",
    "BaseEmbeddingClient": "agent_framework._clients",
    "ChatOptions": "agent_framework._types",
    "ChatAndFunctionMiddlewareTypes": "agent_framework._middleware",
    "ChatMiddlewareLayer": "agent_framework._middleware",
    "ChatResponseUpdate": "agent_framework._types",
    "Content": "agent_framework._types",
    "ContextProvider": "agent_framework._sessions",
    "FileCheckpointStorage": "agent_framework._workflows._checkpoint",
    "Embedding": "agent_framework._types",
    "EmbeddingGenerationOptions": "agent_framework._types",
    "FunctionInvocationConfiguration": "agent_framework._tools",
    "FunctionInvocationLayer": "agent_framework._tools",
    "FunctionTool": "agent_framework._tools",
    "GeneratedEmbeddings": "agent_framework._types",
    "HistoryProvider": "agent_framework._sessions",
    "Message": "agent_framework._types",
    "SessionContext": "agent_framework._sessions",
    "UsageDetails": "agent_framework._types",
    "WorkflowAgent": "agent_framework._workflows._agent",
    "load_settings": "agent_framework._settings",
}.items():
    if not hasattr(_agent_framework, _name):
        setattr(_agent_framework, _name, getattr(importlib.import_module(_module), _name))

from agent_framework._agents import Agent
from agent_framework._mcp import MCPStdioTool
from agent_framework._middleware import function_middleware
from agent_framework._skills import SkillsProvider
from agent_framework._tools import tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import Field

BLUE = "\033[94m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"

DEFAULT_PROJECT_ENDPOINT = "https://cnt-test-gblmitsha-cin-aif.services.ai.azure.com/api/projects/cnt-test-gblmitsha-cin-proj"
DEFAULT_AZURE_SCOPE = "https://ai.azure.com/.default"

warnings.filterwarnings("ignore", message=r"\[SKILLS\].*")


@dataclass
class AgentSettings:
    project_endpoint: str
    model: str
    azure_scope: str
    service_url: str
    access_token: str
    mcp_server_path: Path
    mcp_timeout_seconds: int
    shell_timeout_seconds: int
    verbose: bool


@dataclass
class ScopedAzureCredential:
    credential: DefaultAzureCredential
    scope: str

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        return self.credential.get_token(self.scope, **kwargs)


def color(message: str, code: str) -> str:
    return f"{code}{message}{RESET}"


def log_blue(message: str) -> None:
    print(color(message, BLUE), file=sys.stderr, flush=True)


def log_yellow(message: str) -> None:
    print(color(message, YELLOW), file=sys.stderr, flush=True)


def log_verbose(settings: AgentSettings, message: str) -> None:
    if settings.verbose:
        print(color(f"[verbose] {message}", DIM), file=sys.stderr, flush=True)


def redact_sensitive_values(text: str) -> str:
    text = re.sub(r"(Authorization:\s*Bearer\s+)[^\s\"']+", r"\1<redacted>", text, flags=re.IGNORECASE)
    text = re.sub(r"(--access-token\s+)(\"[^\"]+\"|'[^']+'|\S+)", r"\1<redacted>", text)
    text = re.sub(r"(access_token=)[^&\s\"']+", r"\1<redacted>", text, flags=re.IGNORECASE)
    text = re.sub(r"(accessKey=)[^&\s\"']+", r"\1<redacted>", text, flags=re.IGNORECASE)
    return re.sub(r"wss://[^\s\"']+", "wss://<redacted-cdp-url>", text)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_mcp_server_path() -> Path:
    return project_root() / "azure-playwright-service-mcp" / "src" / "index.js"


def default_skill_path() -> Path:
    return project_root() / "skills"


def normalize_foundry_project_endpoint(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    normalized = endpoint.rstrip("/")
    suffix = "/openai/v1"
    if normalized.endswith(suffix):
        return normalized[: -len(suffix)]
    return normalized


def make_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    environment_scripts_path = Path(sys.executable).parent
    env["PATH"] = str(environment_scripts_path) + os.pathsep + env.get("PATH", "")
    return env


def resolve_browser_use_command(env: dict[str, str]) -> str:
    return shutil.which("browser-use", path=env["PATH"]) or "browser-use"


def decode_subprocess_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def require_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value and not is_template_placeholder(value):
            return value
    raise RuntimeError(f"Missing required environment variable: {' or '.join(names)}")


def is_template_placeholder(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("{{") and stripped.endswith("}}")


def int_env(name: str, default_value: int) -> int:
    value = os.getenv(name)
    if not value or is_template_placeholder(value):
        return default_value
    return int(value)


def make_settings() -> AgentSettings:
    project_endpoint = normalize_foundry_project_endpoint(
        os.getenv("FOUNDRY_PROJECT_ENDPOINT")
        or os.getenv("AZURE_FOUNDRY_PROJECT_ENDPOINT")
        or os.getenv("AZURE_OPENAI_ENDPOINT")
        or DEFAULT_PROJECT_ENDPOINT,
    )
    if not project_endpoint:
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT is required.")

    return AgentSettings(
        project_endpoint=project_endpoint,
        model=require_env("AZURE_AI_MODEL_DEPLOYMENT_NAME", "BROWSER_AGENT_MODEL", "AZURE_OPENAI_DEPLOYMENT_NAME"),
        azure_scope=os.getenv("AZURE_AI_SCOPE") or os.getenv("AZURE_OPENAI_SCOPE") or DEFAULT_AZURE_SCOPE,
        service_url=require_env("AZURE_PLAYWRIGHT_SERVICE_URL", "PLAYWRIGHT_SERVICE_URL"),
        access_token=require_env("AZURE_PLAYWRIGHT_SERVICE_ACCESS_TOKEN", "PLAYWRIGHT_SERVICE_ACCESS_TOKEN"),
        mcp_server_path=Path(os.getenv("BROWSER_AGENT_MCP_SERVER_PATH") or default_mcp_server_path()).resolve(),
        mcp_timeout_seconds=int_env("BROWSER_AGENT_MCP_TIMEOUT_SECONDS", 120),
        shell_timeout_seconds=int_env("BROWSER_AGENT_SHELL_TIMEOUT_SECONDS", 180),
        verbose=os.getenv("BROWSER_AGENT_VERBOSE", "").lower() in {"1", "true", "yes"},
    )


def make_run_shell(settings: AgentSettings):
    @tool(
        name="run_shell",
        description=(
            "Run a shell command with subprocess and return stdout, stderr, and exit code. "
            "Use this for browser-use CLI commands and installation checks."
        ),
    )
    def run_shell(
        command: Annotated[str, Field(description="Shell command to execute.")],
        cwd: Annotated[
            str | None,
            Field(description="Optional working directory. Defaults to the hosted agent app directory."),
        ] = None,
        timeout_seconds: Annotated[
            int | None,
            Field(description="Optional timeout in seconds. Defaults to the agent shell timeout."),
        ] = None,
    ) -> str:
        effective_cwd = Path(cwd).resolve() if cwd else project_root()
        effective_timeout = timeout_seconds or settings.shell_timeout_seconds
        safe_command = redact_sensitive_values(command)
        log_yellow(f"[run_shell] cwd={effective_cwd} timeout={effective_timeout}s command={safe_command}")

        env = make_subprocess_env()
        if os.name == "nt":
            executable = shutil.which("pwsh", path=env["PATH"]) or shutil.which("powershell", path=env["PATH"])
            if not executable:
                return "PowerShell is required to run shell commands on Windows, but it was not found on PATH."
            shell_command: str | list[str] = [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ]
            use_shell = False
        else:
            shell_command = command
            use_shell = True

        try:
            completed = subprocess.run(
                shell_command,
                cwd=str(effective_cwd),
                shell=use_shell,
                capture_output=True,
                timeout=effective_timeout,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as ex:
            stdout = redact_sensitive_values(decode_subprocess_output(ex.stdout))
            stderr = redact_sensitive_values(decode_subprocess_output(ex.stderr))
            return (
                f"Command timed out after {effective_timeout} seconds.\n"
                f"stdout:\n{stdout}\n\nstderr:\n{stderr}"
            )

        stdout = redact_sensitive_values(decode_subprocess_output(completed.stdout))
        stderr = redact_sensitive_values(decode_subprocess_output(completed.stderr))
        return (
            f"exit_code: {completed.returncode}\n"
            f"stdout:\n{stdout or '<empty>'}\n\n"
            f"stderr:\n{stderr or '<empty>'}"
        )

    return run_shell


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
            "Close a browser automation session. This first runs browser-use close to disconnect "
            "the held remote WSS/CDP connection, then calls the Playwright Service MCP end_browser_session tool."
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
        browser_use_command = resolve_browser_use_command(env)
        log_yellow(f"[browser-use] close sessionId={session_id}")

        process = await asyncio.create_subprocess_exec(
            browser_use_command,
            "--session",
            session_id,
            "close",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=settings.shell_timeout_seconds,
            )
            browser_use_timed_out = False
        except asyncio.TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            browser_use_timed_out = True

        browser_use_stdout = redact_sensitive_values(decode_subprocess_output(stdout))
        browser_use_stderr = redact_sensitive_values(decode_subprocess_output(stderr))
        browser_use_exit_code = process.returncode

        log_yellow(f"[MCP] end_browser_session arguments={{'sessionId': '{session_id}'}}")
        mcp_result = await call_mcp_end_browser_session(settings, session_id)

        return json.dumps(
            {
                "sessionId": session_id,
                "browserUseDisconnected": browser_use_exit_code == 0 and not browser_use_timed_out,
                "browserUseTimedOut": browser_use_timed_out,
                "browserUseExitCode": browser_use_exit_code,
                "browserUseStdout": browser_use_stdout.strip(),
                "browserUseStderr": browser_use_stderr.strip(),
                "mcpEndResult": mcp_result,
            },
            indent=2,
        )

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


@function_middleware
async def tool_logging_middleware(context: Any, call_next: Any) -> None:
    function_name = getattr(getattr(context, "function", None), "name", "")
    arguments = getattr(context, "arguments", None)

    if function_name == "load_skill":
        log_blue(f"[skill] load_skill arguments={arguments}")
    elif function_name == "create_browser_session":
        log_blue(f"[MCP] create_browser_session arguments={arguments}")
    elif function_name == "close_browser_session":
        log_yellow(f"[close_browser_session] arguments={arguments}")

    await call_next()


def build_agent(settings: AgentSettings) -> tuple[Agent, MCPStdioTool]:
    credential = ScopedAzureCredential(
        credential=DefaultAzureCredential(),
        scope=settings.azure_scope,
    )
    client = FoundryChatClient(
        project_endpoint=settings.project_endpoint,
        model=settings.model,
        credential=credential,
    )

    skills_provider = SkillsProvider(skill_paths=default_skill_path())
    mcp_tool = make_mcp_tool(settings)
    run_shell = make_run_shell(settings)
    close_browser_session = make_close_browser_session(settings)

    instructions = """
You are a Foundry-hosted browser automation agent.

Use the azure-playwright-browser-automation skill whenever the user asks to
navigate websites, test web pages, fill forms, take screenshots, extract web
data, or perform browser automation. For browser work, use the remote Azure
Playwright Service lifecycle:
1. Load the browser automation skill.
2. Call create_browser_session with a stable sessionId.
3. Run exactly one initial Browser Use connection command with --cdp-url, and
   that command must only open about:blank. Do not navigate to the target URL,
   run eval, add shell environment setup, or chain with && or ; in the same
   command as --cdp-url.
4. After the initial connection succeeds, reuse the same browser-use session for
   all subsequent commands and never pass --cdp-url again.
5. When finished, call close_browser_session. Do not call raw
   end_browser_session; close_browser_session first disconnects Browser Use's
   held WSS/CDP connection and then ends the Playwright Service session.

Use run_shell for command execution. Keep responses concise and include concrete
results from the browser state or command output.
""".strip()

    agent = Agent(
        client=client,
        name="browser-automation-agent-v2-foundry",
        instructions=instructions,
        tools=[run_shell, close_browser_session, mcp_tool],
        context_providers=[skills_provider],
        middleware=[tool_logging_middleware],
        default_options={"store": False},
    )
    return agent, mcp_tool


def main() -> None:
    load_dotenv()
    settings = make_settings()
    log_verbose(settings, f"Foundry project endpoint: {settings.project_endpoint}")
    log_verbose(settings, f"Model: {settings.model}")
    log_verbose(settings, f"MCP server: {settings.mcp_server_path}")
    agent, _ = build_agent(settings)
    ResponsesHostServer(agent).run()


if __name__ == "__main__":
    main()
