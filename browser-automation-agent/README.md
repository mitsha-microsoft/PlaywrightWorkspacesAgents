# Browser Automation Agent

A long-running command-line agent for browser automation. It uses:

- `bu-agent-sdk` for the turn-by-turn agent loop.
- The local `azure-playwright-service-mcp` server to create and end remote Microsoft Playwright Workspaces browser sessions.
- The `browser-use` CLI to interact with the active remote browser through its CDP WebSocket URL.

## Lifecycle

1. The agent calls `create_browser_session` on the MCP server with a `sessionId`.
2. The MCP server returns the Playwright Service `sessionUrl`.
3. The agent attaches Browser Use to that CDP URL once with the same Browser Use session id.
4. The Browser Use daemon stays alive across user turns. Subsequent browser commands reuse the existing Browser Use session and do not reconnect to the Playwright Service session.
5. When asked to close, the agent runs `browser-use --session <sessionId> close`, then calls the MCP `end_browser_session` tool with the same `sessionId`.

This keep-alive behavior is important because Playwright Service sessions can be dropped if the CDP connection is not held open.

## Install

From this directory:

```powershell
uv sync
```

The agent depends on `browser-use>=0.12.6`, which includes the `--cdp-url` option needed to attach to Playwright Service sessions. You can validate the CLI with:

```powershell
uv run browser-use --help
browser-use doctor
```

The CLI must support connecting to an existing browser with:

```powershell
browser-use --session <name> --cdp-url <wss-url> open about:blank
```

If your globally installed `browser-use` does not support `--cdp-url`, run the agent through `uv run` so it uses the project dependency.

You do not need to add a Browser Use executable path when using `uv run`. The agent resolves the `browser-use` executable from the same Python environment running the agent. Use `--browser-use-command` only to override that default, for example when testing a custom Browser Use build.

## Configuration

Set Playwright Service credentials for the MCP server:

```powershell
$env:AZURE_PLAYWRIGHT_SERVICE_URL = "wss://<region>.api.playwright.microsoft.com/playwrightworkspaces/<workspaceId>/browsers"
$env:AZURE_PLAYWRIGHT_SERVICE_ACCESS_TOKEN = "<token>"
```

Set an LLM provider. Azure OpenAI with Entra auth is supported through `DefaultAzureCredential`:

```powershell
$env:BROWSER_AGENT_LLM_PROVIDER = "azure-openai"
$env:AZURE_OPENAI_ENDPOINT = "https://<resource-name>.openai.azure.com/openai/v1/"
$env:AZURE_OPENAI_DEPLOYMENT_NAME = "<deployment-name>"
az login
```

Then run:

```powershell
uv run browser-automation-agent --provider azure-openai
```

If `AZURE_OPENAI_ENDPOINT` is set, the agent defaults to `azure-openai` even when `--provider` is omitted.

`DefaultAzureCredential` can use Azure CLI login, managed identity, Visual Studio Code credentials, or other supported Azure Identity sources. The token scope defaults to `https://ai.azure.com/.default` and can be overridden with `AZURE_OPENAI_SCOPE` or `--azure-openai-scope`. The agent uses the new v1-compatible Azure OpenAI endpoint, calls the Responses API at `/openai/v1/responses`, and does not send an `api-version` query parameter.

OpenAI is also supported:

```powershell
$env:OPENAI_API_KEY = "<key>"
```

Anthropic is also supported:

```powershell
$env:BROWSER_AGENT_LLM_PROVIDER = "anthropic"
$env:ANTHROPIC_API_KEY = "<key>"
```

## Run

```powershell
uv run browser-automation-agent --verbose
```

Or:

```powershell
uv run python -m browser_automation_agent.main --verbose
```

Useful options:

```text
--provider openai|anthropic|azure-openai
--model <model-or-azure-openai-deployment-name>
--azure-openai-endpoint <endpoint>
--azure-openai-scope <aad-scope>
--service-url <Playwright Service WSS URL>
--access-token <token>
--mcp-server-path <path-to-azure-playwright-service-mcp/src/index.js>
--browser-use-command <optional-browser-use-executable-or-command>
--verbose
```

Prefer environment variables or a secret manager for tokens instead of passing `--access-token` on the command line.

## Example conversation

```text
browser-agent> Create a browser session named demo and open https://example.com.
browser-agent> Show me the clickable elements.
browser-agent> Close the browser session.
```

MCP session create/end calls are logged in color:

- Blue: create session
- Yellow: end session

With `--verbose`, the agent also prints the Browser Use CLI commands it executes.
