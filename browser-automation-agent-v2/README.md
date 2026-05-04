# Browser Automation Agent v2

A long-running command-line browser automation agent built with Microsoft Agent Framework.

It uses:

- Microsoft Agent Framework for the turn-by-turn agent loop.
- Azure AI Foundry with `DefaultAzureCredential` for Entra-authenticated LLM access.
- The local `azure-playwright-service-mcp` server to create and end remote Microsoft Playwright Workspaces browser sessions.
- A generic `run_shell` tool so the agent can invoke `browser-use` CLI commands directly.
- Agent Framework skills support to load Browser Use workflow instructions adapted for Azure Playwright Service.

## How it differs from v1

| Area | v1 | v2 |
| --- | --- | --- |
| Agent runtime | `bu-agent-sdk` | Microsoft Agent Framework |
| Browser command surface | Dedicated `browser_use` tool | Generic `run_shell` subprocess tool |
| Browser guidance | Embedded prompt markdown | Agent Framework `SkillsProvider` skill |
| MCP integration | Python MCP client calls | Agent Framework `MCPStdioTool` |
| Shell logging | Verbose-only Browser Use command logs | Yellow log for every `run_shell` call |

## Lifecycle

1. The agent loads the `azure-playwright-browser-automation` skill for browser automation tasks.
2. It calls `create_browser_session` on the MCP server with a `sessionId`.
3. The MCP server returns the Playwright Service `sessionUrl` as `cdpUrl`.
4. The agent uses `run_shell` to connect Browser Use once with a standalone `about:blank` command:

   ```powershell
   browser-use --session <sessionId> --cdp-url "<cdpUrl>" open about:blank
   ```

   This command must not be chained with navigation, `eval`, `state`, or shell environment setup.

5. Subsequent Browser Use commands reuse the same session and do not pass `--cdp-url` again:

   ```powershell
   browser-use --session <sessionId> state
   browser-use --session <sessionId> open https://example.com
   ```

6. When finished, the agent calls `close_browser_session`, which runs `browser-use --session <sessionId> close` to disconnect the held WSS/CDP connection, then calls MCP `end_browser_session`.

## Install

From this directory:

```powershell
uv sync
```

The project depends on `browser-use>=0.12.6`, which includes the `--cdp-url` option needed for Azure Playwright Service. You can verify it with:

```powershell
uv run browser-use --help
uv run browser-use doctor
```

If Browser Use is missing in an existing environment, install it manually:

```powershell
uv pip install browser-use
```

## Configuration

Set Playwright Service credentials for the MCP server:

```powershell
$env:AZURE_PLAYWRIGHT_SERVICE_URL = "wss://<region>.api.playwright.microsoft.com/playwrightworkspaces/<workspaceId>/browsers"
$env:AZURE_PLAYWRIGHT_SERVICE_ACCESS_TOKEN = "<token>"
```

Set Azure AI Foundry model configuration:

```powershell
$env:AZURE_FOUNDRY_PROJECT_ENDPOINT = "https://<resource>.services.ai.azure.com/api/projects/<project>"
$env:BROWSER_AGENT_MODEL = "gpt-5.4"
az login
```

`DefaultAzureCredential` can use Azure CLI login, managed identity, Visual Studio Code credentials, or other supported Azure Identity sources. The token scope defaults to:

```text
https://ai.azure.com/.default
```

Override it with:

```powershell
$env:AZURE_AI_SCOPE = "https://ai.azure.com/.default"
```

For compatibility, `--project-endpoint` also accepts a value ending in `/openai/v1`; the agent removes that suffix before creating `FoundryChatClient`.

## Run

```powershell
uv run browser-automation-agent-v2 --verbose
```

Or:

```powershell
uv run python -m browser_automation_agent_v2.main --verbose
```

Useful options:

```text
--project-endpoint <Azure AI Foundry project endpoint>
--model <model-or-deployment-name>
--azure-scope <aad-scope>
--service-url <Playwright Service WSS URL>
--access-token <token>
--mcp-server-path <path-to-azure-playwright-service-mcp\src\index.js>
--mcp-timeout-seconds <seconds>
--shell-timeout-seconds <seconds>
--verbose
```

Prefer environment variables or a secret manager for tokens instead of passing `--access-token` on the command line.

## Example conversation

```text
browser-agent-v2> Create a browser session named demo and open https://example.com.
browser-agent-v2> Show me the clickable elements.
browser-agent-v2> Close the browser session.
```

## Logging

- Blue: skill load and MCP `create_browser_session` calls.
- Yellow: every `run_shell` call and `close_browser_session` cleanup.

Sensitive values such as bearer tokens, access keys, and CDP WebSocket URLs are redacted from shell command output where possible.
