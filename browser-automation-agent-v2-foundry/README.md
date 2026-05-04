# Browser Automation Agent v2 Foundry

A Foundry-hosted copy of `browser-automation-agent-v2`.

This project packages the browser automation agent as a containerized Microsoft Agent Framework hosted agent. It exposes the Foundry **Responses** protocol on port `8088` through `ResponsesHostServer`.

## What changed from v2

| Area | `browser-automation-agent-v2` | `browser-automation-agent-v2-foundry` |
| --- | --- | --- |
| Runtime | Long-running local CLI REPL | Foundry hosted Responses server |
| Entrypoint | `browser-automation-agent-v2` | `python -m browser_automation_agent_v2_foundry.main` |
| Hosting package | Not used | `agent-framework-foundry-hosting` |
| Conversation history | Agent session in the CLI process | Managed by Foundry hosting (`store: false`) |
| MCP server | Sibling local project | Embedded copy under `azure-playwright-service-mcp` |
| Container | Not required | Python 3.12 slim image with Node.js 22 |

The agent still uses:

- Agent Framework `SkillsProvider`.
- Azure Playwright Service MCP for `create_browser_session`.
- A v2-only `close_browser_session` wrapper that disconnects Browser Use before calling MCP `end_browser_session`.
- Browser Use CLI through `run_shell`.

## Configuration

Required environment variables:

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = "https://cnt-test-gblmitsha-cin-aif.services.ai.azure.com/api/projects/cnt-test-gblmitsha-cin-proj"
$env:AZURE_AI_MODEL_DEPLOYMENT_NAME = "gpt-5.4"
$env:AZURE_PLAYWRIGHT_SERVICE_URL = "wss://eastus.api.playwright.microsoft.com/playwrightworkspaces/<workspace-id>/browsers"
$env:AZURE_PLAYWRIGHT_SERVICE_ACCESS_TOKEN = "<token>"
```

Optional:

```powershell
$env:AZURE_AI_SCOPE = "https://ai.azure.com/.default"
$env:BROWSER_AGENT_SHELL_TIMEOUT_SECONDS = "180"
$env:BROWSER_AGENT_MCP_TIMEOUT_SECONDS = "120"
$env:BROWSER_AGENT_VERBOSE = "true"
```

Do not commit `.env` files or access tokens.

For hosted deployment, custom environment variables are registered from `agent.yaml`. Keep secrets out of tracked files; store them in the local azd environment or another secure deployment-time source.

## Run locally with Python

```powershell
uv sync
uv run browser-automation-agent-v2-foundry
```

The server listens on `http://localhost:8088`.

Invoke it:

```powershell
(Invoke-WebRequest -Uri http://localhost:8088/responses -Method POST -ContentType "application/json" -Body '{"input": "Hello!"}').Content
```

## Run locally with azd

```powershell
azd ai agent run
azd ai agent invoke --local "Hello!"
```

## Deploy to Foundry

This project includes `agent.manifest.yaml` for the Azure Developer CLI hosted-agent workflow.

Initialize against the existing Foundry project:

```powershell
azd ai agent init `
  -m .\agent.manifest.yaml `
  --src . `
  --project-id "/subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.CognitiveServices/accounts/<account>/projects/<project>" `
  --model-deployment "gpt-5.4"
```

Set environment values in your azd environment:

```powershell
azd env set AZURE_PLAYWRIGHT_SERVICE_URL "wss://eastus.api.playwright.microsoft.com/playwrightworkspaces/<workspace-id>/browsers"
azd env set AZURE_PLAYWRIGHT_SERVICE_ACCESS_TOKEN "<token>"
azd env set BROWSER_AGENT_SHELL_TIMEOUT_SECONDS "180"
azd env set BROWSER_AGENT_MCP_TIMEOUT_SECONDS "120"
azd env set AZURE_CONTAINER_REGISTRY_ENDPOINT "<registry>.azurecr.io"
```

Deploy:

```powershell
azd deploy browser-automation-agent-v2-foundry
```

If your `azd` preview extension leaves `{{...}}` placeholders literal in `agent.yaml`, substitute them from the local azd environment only for the deploy command and restore `agent.yaml` immediately afterward. Do not commit a resolved `agent.yaml` containing tokens.

Monitor or invoke:

```powershell
azd ai agent show
azd ai agent invoke --new-session "Go to https://python.org and report the latest Python download version."
```

The deployed agent can also be opened from the Foundry portal Agent playground URL emitted by `azd deploy`.
