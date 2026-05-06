---
name: azure-playwright-browser-automation
description: Automates browser interactions for web testing, form filling, screenshots, and data extraction using Playwright CLI connected to a remote Azure Playwright Service browser through MCP.
allowed-tools: run_playwright_cli, create_browser_session, close_browser_session
---

# Browser automation with Playwright CLI and Azure Playwright Service

This skill is the operational reference for browser tasks. The base prompt owns
the non-negotiable lifecycle rules; this skill provides the concrete Playwright
CLI command patterns.

## Remote browser connection

1. Call `create_browser_session` with a stable `sessionId`.
2. Read the returned `cdpUrl`.
3. Call `run_playwright_cli` with the same `sessionId`, the returned `cdpUrl`,
   and the command:

   ```text
   open about:blank
   ```

   The tool sets `PLAYWRIGHT_MCP_CDP_ENDPOINT=<cdpUrl>` before invoking
   `playwright-cli -s=<sessionId> open about:blank`.

   This must be a standalone handshake command. Do not combine it with target
   navigation, `eval`, `snapshot`, or any other browser operation.
4. Run all subsequent commands with the same `sessionId` and no `cdpUrl`:

   ```text
   goto https://example.com
   snapshot
   ```

5. Call `close_browser_session` when finished. It detaches Playwright CLI from
   the named session, then ends the Playwright Service browser through MCP.

If the initial `open about:blank` command with `cdpUrl` fails, do not retry the
same CDP URL repeatedly. Call `close_browser_session`, then create a fresh remote
session with a new `sessionId`.

## Installation check

The hosted container installs `@playwright/cli` and the packaged Playwright CLI
skills at build time:

```bash
npm install -g @playwright/cli@latest
playwright-cli install --skills
```

If running locally, verify the CLI before browser work:

```bash
playwright-cli --help
```

## Common commands

Pass only the arguments shown below to `run_playwright_cli.command`; do not
include `playwright-cli` or `-s=<sessionId>`.

```bash
# Navigation
open
open https://example.com
goto https://playwright.dev
go-back
go-forward
reload

# Page state
snapshot
snapshot --filename=after-click.yaml
snapshot --depth=4
screenshot
screenshot --filename=page.png

# Interactions
click e3
dblclick e7
fill e5 "user@example.com"
fill e5 "search text" --submit
type "search query"
press Enter
hover e4
select e9 "option-value"
check e12
uncheck e12

# Tabs
tab-list
tab-new https://example.com/other
tab-select 0
tab-close

# Extraction and diagnostics
eval "document.title"
eval "JSON.stringify([...document.querySelectorAll('a')].map(a => a.href))"
console
requests
request 5
```

After most commands, Playwright CLI emits page status and a snapshot. Use refs
from the snapshot, such as `e15`, for subsequent interactions.

Use `--raw` inside the command when you need only a result value:

```text
--raw eval "document.title"
```

## Cleanup

Always call `close_browser_session` with:

```json
{ "sessionId": "<sessionId>" }
```

This detaches Playwright CLI from the held WSS/CDP connection for the named
session, then ends the remote browser through the MCP server.

