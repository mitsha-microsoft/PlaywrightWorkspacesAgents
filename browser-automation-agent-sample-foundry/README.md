# Browser Automation Agent Sample for Microsoft Foundry

This sample shows how to build a Foundry-hosted browser automation agent with
Microsoft Agent Framework, Foundry Toolbox, Azure Playwright Service, and
Playwright CLI.

The sample is designed to be easy to tailor. The runtime code is shared, while
the agent behavior is selected with small prompt profiles such as `general`,
`web-scraper`, `form-filler`, and `qa-tester`.

## Solution overview

The agent runs as a Foundry hosted agent using the **Responses** protocol. When a
user asks for browser work, the agent:

1. Connects to a Foundry Toolbox MCP endpoint in the same Foundry project.
2. Calls `create_session` from that Toolbox to provision a remote Chromium browser.
3. Connects Playwright CLI to the returned CDP WebSocket URL.
4. Uses `run_playwright_cli` to invoke Playwright CLI commands.
5. Calls `close_browser_session` to detach Playwright CLI state and end the
   remote browser.

```text
User
  -> Foundry hosted agent
      -> Agent Framework tools
          -> Foundry Toolbox MCP create_session
              -> Azure Playwright Service remote Chromium
          -> Playwright CLI
              -> remote browser CDP session
```

## Key features

- **Foundry hosted agent**: containerized Agent Framework app exposed through
  `ResponsesHostServer` on port `8088`.
- **Remote browser sessions**: Azure Playwright Service browser provisioning via
  a governed Foundry Toolbox MCP endpoint.
- **Profile-based specialization**: select `general`, `web-scraper`,
  `form-filler`, or `qa-tester` without changing Python code.
- **Concrete browser skill**: a Playwright CLI skill documents the exact remote
  browser connection and cleanup workflow.
- **Playwright CLI installed in the image**: the Docker build installs
  `@playwright/cli` and runs `playwright-cli install --skills`.
- **Safe cleanup path**: `close_browser_session` detaches the named Playwright
  CLI session and then closes the remote browser.
- **Colored tool logs**: Toolbox and skill events log in blue; Playwright CLI and
  cleanup events log in yellow.

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/browser_automation_agent_sample_foundry/` | Shared Python implementation for hosting, settings, prompts, tools, and agent construction. |
| `prompts/base.md` | Shared lifecycle, safety, and cleanup rules. |
| `prompts/profiles/` | User-editable profiles for specialization. |
| `skills/azure-playwright-browser-automation/SKILL.md` | Playwright CLI operational reference for remote Azure Playwright Service sessions. |
| `azure-playwright-service-mcp/` | Optional local MCP helper retained for local fallback cleanup scenarios. |
| `docs/sample-structure.md` | Design notes explaining the sample structure and extension points. |

## Prerequisites

- A Microsoft Foundry project with a deployed model.
- Azure Developer CLI with the Foundry AI extension.
- Docker, if you want to build the container locally.
- A Foundry Toolbox deployed in the same project with a `create_session` MCP tool.

For hosted-agent setup, see
[Deploy hosted agents with azd](https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/quickstart-hosted-agent?pivots=azd).

## Configuration

Copy `.env.example` to `.env` for local development, or set these values in your
azd environment for deployment:

```powershell
azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME "gpt-4o-mini"
azd env set BROWSER_AGENT_TOOLBOX_NAME "<toolbox-name>"
azd env set BROWSER_AGENT_PROFILE "web-scraper"
# Optional: use a custom prompt file instead of prompts/profiles/<profile>.md.
# azd env set BROWSER_AGENT_PROMPT_FILE "prompts/profiles/web-scraper.md"
azd env set BROWSER_AGENT_PLAYWRIGHT_CLI_TIMEOUT_SECONDS "180"
azd env set BROWSER_AGENT_MCP_TIMEOUT_SECONDS "120"
```

If your environment requires an existing Azure Container Registry:

```powershell
azd env set AZURE_CONTAINER_REGISTRY_ENDPOINT "<registry>.azurecr.io"
```

Do not commit `.env`, `.azure`, or files containing access tokens.

The Toolbox endpoint is resolved as
`<FOUNDRY_PROJECT_ENDPOINT>/toolboxes/<BROWSER_AGENT_TOOLBOX_NAME>/mcp?api-version=v1`
and authenticated with the hosted agent identity.

## Choose a profile

Profiles live in `prompts/profiles/`.

| Profile | Use for |
| --- | --- |
| `general` | Broad browser automation tasks. |
| `web-scraper` | Structured web extraction and reporting. |
| `form-filler` | Inspecting, filling, validating, and optionally submitting forms. |
| `qa-tester` | Exploratory web testing and bug reporting. |

Set the profile before deployment:

```powershell
azd env set BROWSER_AGENT_PROFILE "form-filler"
```

To add a profile, copy one of the Markdown files in `prompts/profiles/`, edit it,
and set `BROWSER_AGENT_PROFILE` to the new file name without `.md`.

## Run locally

Install dependencies:

```powershell
uv sync --prerelease allow
npm install -g @playwright/cli@latest
playwright-cli install --skills
npm --prefix azure-playwright-service-mcp install
```

Run the hosted-agent server locally:

```powershell
uv run browser-automation-agent-sample-foundry
```

Invoke the local Responses endpoint:

```powershell
(Invoke-WebRequest `
  -Uri http://localhost:8088/responses `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"input": "Open https://example.com and report the page title."}').Content
```

You can also use azd:

```powershell
azd ai agent run
azd ai agent invoke --local --new-session "Open https://example.com and report the page title."
```

## Deploy to Foundry

Initialize the hosted-agent project against an existing Foundry project:

```powershell
azd ai agent init `
  -m .\agent.manifest.yaml `
  --src . `
  --project-id "/subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.CognitiveServices/accounts/<account>/projects/<project>" `
  --model-deployment "gpt-4o-mini"
```

Deploy:

```powershell
azd deploy browser-automation-agent-sample-foundry
```

Invoke:

```powershell
azd ai agent invoke browser-automation-agent-sample-foundry `
  --new-session `
  "Use the remote browser to open https://example.com, report the page title, and close the browser session."
```

Monitor logs:

```powershell
azd ai agent monitor browser-automation-agent-sample-foundry --tail 100
```

## Notes for azd preview builds

Some preview versions of the Foundry azd extension may leave custom
`{{VARIABLE}}` placeholders literal in `agent.yaml`. If that happens, substitute
custom environment values only during deployment and restore `agent.yaml`
afterward. Never commit a resolved file containing access tokens.

This sample currently targets preview Agent Framework / Foundry hosting
packages. The small compatibility shim in `src/.../compat.py` bridges known
preview export differences and can be removed once the packages expose those
symbols consistently.

## Customize the sample

- Change broad behavior by editing or adding files under `prompts/profiles/`.
- Change non-negotiable lifecycle or safety rules in `prompts/base.md`.
- Add deeper procedural knowledge as skills under `skills/`.
- Add new tools in `src/browser_automation_agent_sample_foundry/tools.py`.

See [docs/sample-structure.md](docs/sample-structure.md) for the design rationale.

## Guidance

This sample is intended as a starting point, not a production-ready browser
automation platform. Before using it in production, review authentication,
network access, data handling, secret management, logging, browser permissions,
and approval flows for state-changing actions.

The `run_playwright_cli` tool intentionally invokes only `playwright-cli` with a
named session and optional `PLAYWRIGHT_MCP_CDP_ENDPOINT`; it does not expose
general shell execution.

The Docker build also runs `playwright-cli install --skills`, which installs the
packaged Playwright CLI skill under `.claude/skills`. The Agent Framework
`SkillsProvider` loads both this installed skill folder and the sample-specific
`skills/azure-playwright-browser-automation` folder.

The default hosted container resources (`cpu: "0.25"`, `memory: 0.5Gi`) are
minimal. Increase them in `agent.yaml` and `azure.yaml` for multi-step scraping,
longer QA sessions, or data-heavy browser automation.

Useful references:

- [Hosted agents in Microsoft Foundry](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents)
- [Agent Framework overview](https://learn.microsoft.com/en-gb/agent-framework/overview/?pivots=programming-language-python)
- [Agent Framework skills](https://learn.microsoft.com/en-gb/agent-framework/agents/skills?pivots=programming-language-python)
- [Playwright CLI](https://github.com/microsoft/playwright-cli)

