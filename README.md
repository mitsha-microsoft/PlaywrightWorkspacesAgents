# Playwright Sample MCP Workspace

This workspace contains related projects for remote browser automation with Microsoft Playwright Workspaces.

## Projects

| Project | Purpose | Runtime |
| --- | --- | --- |
| [`azure-playwright-service-mcp`](.\azure-playwright-service-mcp) | MCP server that creates and ends remote Chromium browser sessions through Azure Playwright Service. | Node.js |
| [`browser-automation-agent`](.\browser-automation-agent) | Long-running command-line agent that uses the MCP server plus Browser Use CLI to automate the remote browser turn by turn. | Python |
| [`browser-automation-agent-v2`](.\browser-automation-agent-v2) | Long-running command-line agent built with Microsoft Agent Framework, Agent Framework skills, the MCP server, and a generic shell tool for Browser Use CLI automation. | Python |
| [`browser-automation-agent-v2-foundry`](.\browser-automation-agent-v2-foundry) | Foundry-hosted container version of the v2 agent, exposing the Responses protocol through Microsoft Agent Framework hosting. | Python + Docker |
| [`browser-automation-agent-sample-foundry`](.\browser-automation-agent-sample-foundry) | Publishable Foundry-hosted browser automation sample with shared runtime code, Playwright CLI tooling, and selectable prompt profiles for general browsing, scraping, form filling, and QA testing. | Python + Docker |

## `azure-playwright-service-mcp`

This project exposes a Model Context Protocol server with two tools:

- `create_browser_session`
- `end_browser_session`

Both tools accept a `sessionId`. The server converts the configured Playwright Service WebSocket endpoint into the HTTPS provisioning endpoint, passes `runId=<sessionId>`, and extracts the returned `sessionUrl` CDP WebSocket URL.

The MCP server is stateless: it uses the same `sessionId`/`runId` to create or resolve a browser session, then uses CDP `Browser.close` when ending a session.

See [`azure-playwright-service-mcp\README.md`](.\azure-playwright-service-mcp\README.md) for configuration and MCP client usage.

## `browser-automation-agent`

This project is a command-line browser automation agent built with `bu-agent-sdk`.

It:

- Starts the local `azure-playwright-service-mcp` server over stdio when it needs MCP tools.
- Creates a Playwright Service browser session through MCP.
- Attaches Browser Use CLI to the returned CDP URL once.
- Keeps the Browser Use session alive across turns.
- Closes Browser Use and then calls MCP `end_browser_session` when the user asks to end the browser session.

The agent supports Azure OpenAI through Entra auth with `DefaultAzureCredential` and the Azure AI Foundry/OpenAI v1 Responses API.

See [`browser-automation-agent\README.md`](.\browser-automation-agent\README.md) for setup and run instructions.

## `browser-automation-agent-v2`

This project is a command-line browser automation agent built with Microsoft Agent Framework.

It:

- Uses `FoundryChatClient` with `DefaultAzureCredential` for Azure AI Foundry model access.
- Registers the local `azure-playwright-service-mcp` server through Agent Framework `MCPStdioTool`.
- Provides a generic `run_shell` tool that the agent uses to run Browser Use CLI commands.
- Loads an Agent Framework skill adapted from the Browser Use CLI skill for Azure Playwright Service remote browser sessions.
- Logs skill/MCP session creation in blue and `run_shell`/session cleanup in yellow.

See [`browser-automation-agent-v2\README.md`](.\browser-automation-agent-v2\README.md) for setup and run instructions.

## `browser-automation-agent-v2-foundry`

This project packages the v2 Microsoft Agent Framework agent as a Foundry hosted agent.

It:

- Runs `ResponsesHostServer` on port `8088` inside a container.
- Embeds the Azure Playwright Service MCP server so the hosted agent is self-contained.
- Uses the same Browser Use CLI skill and `run_shell` flow as `browser-automation-agent-v2`.
- Deploys with `azd ai agent init` and `azd deploy`.

See [`browser-automation-agent-v2-foundry\README.md`](.\browser-automation-agent-v2-foundry\README.md) for local hosting and Foundry deployment instructions.

## `browser-automation-agent-sample-foundry`

This project is the sample-oriented version of the hosted browser automation agent.

It:

- Keeps Foundry hosting, MCP wiring, Playwright CLI execution, logging, and cleanup in one shared Python implementation.
- Composes `prompts\base.md` with a selectable profile under `prompts\profiles\`.
- Ships starter profiles for `general`, `web-scraper`, `form-filler`, and `qa-tester`.
- Embeds the Azure Playwright Service MCP server inside the hosted container.

See [`browser-automation-agent-sample-foundry\README.md`](.\browser-automation-agent-sample-foundry\README.md) for the sample quickstart, and [`browser-automation-agent-sample-foundry\docs\sample-structure.md`](.\browser-automation-agent-sample-foundry\docs\sample-structure.md) for the design rationale.

## High-level flow

```text
User
  -> browser-automation-agent, browser-automation-agent-v2, browser-automation-agent-v2-foundry, or browser-automation-agent-sample-foundry
      -> azure-playwright-service-mcp
          -> Azure Playwright Service
      -> browser-use CLI
          -> remote browser CDP session
```

The MCP server handles browser session provisioning and cleanup. The agent handles the interactive automation loop and delegates concrete browser actions to Browser Use CLI.
