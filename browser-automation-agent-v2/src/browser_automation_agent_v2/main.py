from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from agent_framework import MCPStdioTool, SkillsProvider, function_middleware, tool
from agent_framework.foundry import FoundryChatClient
from azure.identity.aio import DefaultAzureCredential
from pydantic import Field

BLUE = "\033[94m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"

DEFAULT_AZURE_SCOPE = "https://ai.azure.com/.default"

warnings.filterwarnings("ignore", message=r"\[SKILLS\].*")


@dataclass
class AgentSettings:
    project_endpoint: str
    model: str
    azure_scope: str
    service_url: str | None
    access_token: str | None
    mcp_server_path: Path
    mcp_timeout_seconds: int
    shell_timeout_seconds: int
    verbose: bool


@dataclass
class ScopedAzureCredential:
    credential: DefaultAzureCredential
    scope: str

    async def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        return await self.credential.get_token(self.scope, **kwargs)


def color(message: str, code: str) -> str:
    return f"{code}{message}{RESET}"


def log_blue(message: str) -> None:
    print(color(message, BLUE), file=sys.stderr)


def log_yellow(message: str) -> None:
    print(color(message, YELLOW), file=sys.stderr)


def log_verbose(settings: AgentSettings, message: str) -> None:
    if settings.verbose:
        print(color(f"[verbose] {message}", DIM), file=sys.stderr)


def redact_sensitive_values(text: str) -> str:
    text = re.sub(r"(Authorization:\s*Bearer\s+)[^\s\"']+", r"\1<redacted>", text, flags=re.IGNORECASE)
    text = re.sub(r"(--access-token\s+)(\"[^\"]+\"|'[^']+'|\S+)", r"\1<redacted>", text)
    text = re.sub(r"(access_token=)[^&\s\"']+", r"\1<redacted>", text, flags=re.IGNORECASE)
    text = re.sub(r"(accessKey=)[^&\s\"']+", r"\1<redacted>", text, flags=re.IGNORECASE)
    return re.sub(r"wss://[^\s\"']+", "wss://<redacted-cdp-url>", text)


def default_mcp_server_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "azure-playwright-service-mcp"
        / "src"
        / "index.js"
    )


def default_skill_path() -> Path:
    return Path(__file__).resolve().parents[2] / "skills"


def normalize_foundry_project_endpoint(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    normalized = endpoint.rstrip("/")
    suffix = "/openai/v1"
    if normalized.endswith(suffix):
        return normalized[: -len(suffix)]
    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Long-running browser automation agent v2 backed by Microsoft Agent Framework.",
    )
    parser.add_argument(
        "--project-endpoint",
        default=os.getenv("AZURE_FOUNDRY_PROJECT_ENDPOINT")
        or os.getenv("AZURE_AI_PROJECT_ENDPOINT")
        or os.getenv("AZURE_OPENAI_ENDPOINT"),
        help=(
            "Azure AI Foundry project endpoint, for example "
            "https://<resource>.services.ai.azure.com/api/projects/<project>. "
            "For compatibility, a value ending in /openai/v1 is normalized by removing that suffix."
        ),
    )
    parser.add_argument(
        "--model",
        default=os.getenv("BROWSER_AGENT_MODEL")
        or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
        or os.getenv("AZURE_AI_MODEL")
        or "gpt-4o",
        help="Model/deployment name to use with Azure AI Foundry.",
    )
    parser.add_argument(
        "--azure-scope",
        default=os.getenv("AZURE_OPENAI_SCOPE") or os.getenv("AZURE_AI_SCOPE") or DEFAULT_AZURE_SCOPE,
        help="AAD token scope used by DefaultAzureCredential.",
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
        help="Path to azure-playwright-service-mcp\\src\\index.js.",
    )
    parser.add_argument(
        "--mcp-timeout-seconds",
        type=int,
        default=int(os.getenv("BROWSER_AGENT_MCP_TIMEOUT_SECONDS", "120")),
        help="Timeout for MCP requests.",
    )
    parser.add_argument(
        "--shell-timeout-seconds",
        type=int,
        default=int(os.getenv("BROWSER_AGENT_SHELL_TIMEOUT_SECONDS", "180")),
        help="Default timeout for run_shell commands.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=os.getenv("BROWSER_AGENT_VERBOSE", "").lower() in {"1", "true", "yes"},
        help="Print additional setup details.",
    )
    return parser.parse_args()


def make_settings(args: argparse.Namespace) -> AgentSettings:
    project_endpoint = normalize_foundry_project_endpoint(args.project_endpoint)
    if not project_endpoint:
        raise ValueError(
            "Azure AI Foundry project endpoint is required. Pass --project-endpoint "
            "or set AZURE_FOUNDRY_PROJECT_ENDPOINT.",
        )
    if not args.service_url:
        raise ValueError(
            "Playwright Service URL is required. Pass --service-url or set AZURE_PLAYWRIGHT_SERVICE_URL.",
        )
    if not args.access_token:
        raise ValueError(
            "Playwright Service access token is required. Pass --access-token or set "
            "AZURE_PLAYWRIGHT_SERVICE_ACCESS_TOKEN.",
        )

    return AgentSettings(
        project_endpoint=project_endpoint,
        model=args.model,
        azure_scope=args.azure_scope,
        service_url=args.service_url,
        access_token=args.access_token,
        mcp_server_path=args.mcp_server_path.resolve(),
        mcp_timeout_seconds=args.mcp_timeout_seconds,
        shell_timeout_seconds=args.shell_timeout_seconds,
        verbose=args.verbose,
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
            Field(description="Optional working directory. Defaults to the current repository."),
        ] = None,
        timeout_seconds: Annotated[
            int | None,
            Field(description="Optional timeout in seconds. Defaults to the agent shell timeout."),
        ] = None,
    ) -> str:
        effective_cwd = Path(cwd).resolve() if cwd else Path.cwd()
        effective_timeout = timeout_seconds or settings.shell_timeout_seconds
        safe_command = redact_sensitive_values(command)
        log_yellow(f"[run_shell] cwd={effective_cwd} timeout={effective_timeout}s command={safe_command}")

        try:
            completed = subprocess.run(
                command,
                cwd=str(effective_cwd),
                shell=True,
                text=True,
                capture_output=True,
                timeout=effective_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as ex:
            stdout = redact_sensitive_values(ex.stdout or "")
            stderr = redact_sensitive_values(ex.stderr or "")
            return (
                f"Command timed out after {effective_timeout} seconds.\n"
                f"stdout:\n{stdout}\n\nstderr:\n{stderr}"
            )

        stdout = redact_sensitive_values(completed.stdout)
        stderr = redact_sensitive_values(completed.stderr)
        return (
            f"exit_code: {completed.returncode}\n"
            f"stdout:\n{stdout or '<empty>'}\n\n"
            f"stderr:\n{stderr or '<empty>'}"
        )

    return run_shell


def make_mcp_tool(settings: AgentSettings) -> MCPStdioTool:
    env = os.environ.copy()
    if settings.service_url:
        env["AZURE_PLAYWRIGHT_SERVICE_URL"] = settings.service_url
    if settings.access_token:
        env["AZURE_PLAYWRIGHT_SERVICE_ACCESS_TOKEN"] = settings.access_token

    return MCPStdioTool(
        name="azure-playwright-service",
        command="node",
        args=[str(settings.mcp_server_path)],
        env=env,
        request_timeout=settings.mcp_timeout_seconds,
        allowed_tools={"create_browser_session", "end_browser_session"},
        load_prompts=False,
        description="Creates and ends Azure Playwright Service remote browser sessions.",
    )


@function_middleware
async def tool_logging_middleware(context: Any, call_next: Any) -> None:
    function_name = getattr(getattr(context, "function", None), "name", "")
    arguments = getattr(context, "arguments", None)

    if function_name == "load_skill":
        log_blue(f"[skill] load_skill arguments={arguments}")
    elif function_name == "create_browser_session":
        log_blue(f"[MCP] create_browser_session arguments={arguments}")
    elif function_name == "end_browser_session":
        log_yellow(f"[MCP] end_browser_session arguments={arguments}")

    await call_next()


def build_agent(settings: AgentSettings, credential: DefaultAzureCredential):
    scoped_credential = ScopedAzureCredential(credential=credential, scope=settings.azure_scope)
    client = FoundryChatClient(
        project_endpoint=settings.project_endpoint,
        model=settings.model,
        credential=scoped_credential,
    )

    skills_provider = SkillsProvider(skill_paths=default_skill_path())
    mcp_tool = make_mcp_tool(settings)
    run_shell = make_run_shell(settings)

    instructions = """
You are a long-running browser automation CLI agent.

Use the azure-playwright-browser-automation skill whenever the user asks to
navigate websites, test web pages, fill forms, take screenshots, extract web
data, or perform browser automation. For browser work, prefer the remote Azure
Playwright Service lifecycle:
1. Load the browser automation skill.
2. Call create_browser_session with a stable sessionId.
3. Use run_shell to invoke browser-use with --session and the returned --cdp-url.
4. Reuse the same browser-use session for subsequent commands.
5. When finished, close browser-use and call end_browser_session.

Use run_shell for command execution. Keep responses concise and include concrete
results from the browser state or command output.
""".strip()

    agent = client.as_agent(
        name="browser-automation-agent-v2",
        instructions=instructions,
        tools=[run_shell, mcp_tool],
        context_providers=[skills_provider],
        middleware=[tool_logging_middleware],
    )
    return agent, mcp_tool


async def repl(settings: AgentSettings) -> None:
    credential = DefaultAzureCredential()
    mcp_tool: MCPStdioTool | None = None
    try:
        agent, mcp_tool = build_agent(settings, credential)
        session = agent.create_session()

        log_verbose(settings, f"Foundry project endpoint: {settings.project_endpoint}")
        log_verbose(settings, f"Model: {settings.model}")
        log_verbose(settings, f"MCP server: {settings.mcp_server_path}")
        print("browser-agent-v2> Ready. Type 'exit' or 'quit' to stop.")

        while True:
            try:
                user_input = input("browser-agent-v2> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit"}:
                break

            try:
                response = await agent.run(user_input, session=session)
            except Exception as ex:
                print(f"Error: {ex}", file=sys.stderr)
                continue

            text = getattr(response, "text", None) or getattr(response, "value", None) or str(response)
            if text:
                print(text)
    finally:
        if mcp_tool is not None:
            await mcp_tool.close()
        await credential.close()


async def async_main() -> None:
    settings = make_settings(parse_args())
    await repl(settings)


def run() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    run()
