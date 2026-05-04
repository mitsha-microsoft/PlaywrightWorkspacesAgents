---
name: azure-playwright-browser-automation
description: Automates browser interactions for web testing, form filling, screenshots, and data extraction using Browser Use CLI connected to a remote Azure Playwright Service browser through MCP. Use when the user needs to navigate websites, interact with web pages, fill forms, take screenshots, test web applications, or extract information from web pages.
allowed-tools: run_shell, create_browser_session, close_browser_session
---

# Browser Automation with browser-use CLI and Azure Playwright Service

The `browser-use` command provides fast, persistent browser automation. A background daemon keeps the browser open across commands, giving low latency per call.

This agent uses Azure Playwright Service for the browser itself. Do not launch a local Browser Use browser for automation tasks unless the user explicitly asks for local browsing. Instead:

1. Call the `create_browser_session` MCP tool with a stable `sessionId`.
2. Read the returned `cdpUrl`.
3. Use `run_shell` to connect Browser Use to that CDP URL once. This first command is only a connection handshake and must open `about:blank`:

   ```bash
   browser-use --session <sessionId> --cdp-url "<cdpUrl>" open about:blank
   ```

   Do not combine this command with navigation, `eval`, `state`, `&&`, `;`, pipes, or any other browser operation. Do not open the target website in the same command that passes `--cdp-url`.
   Do not add shell-specific environment-variable setup such as `PYTHONIOENCODING=...` or `$env:PYTHONIOENCODING=...`; the `run_shell` tool already sets UTF-8 output handling.

4. After the `about:blank` connection command succeeds, run the target browser work in separate commands that reuse the same Browser Use session without `--cdp-url`:

   ```bash
   browser-use --session <sessionId> state
   browser-use --session <sessionId> open https://example.com
   ```

5. When the browser task is complete, call the `close_browser_session` tool with the same `sessionId`.

   Do not call `end_browser_session` directly. `close_browser_session` first runs `browser-use --session <sessionId> close` to disconnect Browser Use's held WSS/CDP connection, then calls MCP `end_browser_session`.

Keep the Browser Use daemon alive across turns. Do not repeatedly reconnect to the same Azure Playwright Service CDP URL; connect once to `about:blank`, then reuse the Browser Use session.

## Installation

Before browser automation, verify Browser Use is installed:

```bash
browser-use doctor
```

Manual installation:

```bash
# 1. Install the package
uv pip install browser-use
```

The CLI must support the `--cdp-url` global option. If `browser-use --help` does not show `--cdp-url`, install or upgrade Browser Use before trying to attach to the remote browser.

For setup details, see https://github.com/browser-use/browser-use/blob/main/browser_use/skill_cli/README.md

## Core Workflow

1. **Create remote browser**: call `create_browser_session` with a `sessionId`.
2. **Connect Browser Use**: `browser-use --session <sessionId> --cdp-url "<cdpUrl>" open about:blank`. This must be a standalone command with no `&&`, `;`, pipe, `eval`, `state`, target URL, or environment setup.
3. **Navigate**: `browser-use --session <sessionId> open <url>`. Do not pass `--cdp-url` again.
4. **Inspect**: `browser-use --session <sessionId> state` returns clickable elements with indices.
5. **Interact**: use indices from state (`browser-use --session <sessionId> click 5`, `browser-use --session <sessionId> input 3 "text"`).
6. **Verify**: `browser-use --session <sessionId> state` or `browser-use --session <sessionId> screenshot` to confirm.
7. **Repeat**: browser stays open between commands.
8. **Cleanup**: call `close_browser_session` with the same `sessionId`.

If the initial `--cdp-url ... open about:blank` connection command fails, do not retry the same CDP URL repeatedly. Call `close_browser_session`, then create a fresh remote session with a new `sessionId`.

If a later command fails after the initial connection succeeded, run `browser-use --session <sessionId> state` and inspect the page before retrying. Only create a fresh remote session if the Browser Use daemon or remote browser is broken.

Do not use `browser-use connect` or `browser-use cloud connect` for the default workflow. Azure Playwright Service MCP is the cloud-browser provider for this agent.

## Browser Modes

```bash
browser-use open <url>                                      # Local default: headless Chromium
browser-use --headed open <url>                             # Local visible window
browser-use connect                                         # Local user's Chrome
browser-use cloud connect                                   # Browser Use cloud
browser-use --profile "Default" open <url>                  # Local Chrome profile
browser-use --session <name> --cdp-url "<cdpUrl>" open about:blank # Azure Playwright Service connection handshake
```

For this agent, prefer the Azure Playwright Service remote browser mode.

## Commands

```bash
# Navigation
browser-use --session <sessionId> open <url>                    # Navigate to URL
browser-use --session <sessionId> back                          # Go back in history
browser-use --session <sessionId> scroll down                   # Scroll down (--amount N for pixels)
browser-use --session <sessionId> scroll up                     # Scroll up
browser-use --session <sessionId> tab list                      # List all tabs
browser-use --session <sessionId> tab new [url]                 # Open a new tab
browser-use --session <sessionId> tab switch <index>            # Switch to tab by index
browser-use --session <sessionId> tab close <index> [index...]  # Close one or more tabs

# Page State - always run state first to get element indices
browser-use --session <sessionId> state                         # URL, title, clickable elements with indices
browser-use --session <sessionId> screenshot [path.png]         # Screenshot (base64 if no path, --full for full page)

# Interactions - use indices from state
browser-use --session <sessionId> click <index>                 # Click element by index
browser-use --session <sessionId> click <x> <y>                 # Click at pixel coordinates
browser-use --session <sessionId> type "text"                   # Type into focused element
browser-use --session <sessionId> input <index> "text"          # Click element, clear existing text, then type
browser-use --session <sessionId> input <index> ""              # Clear a field without typing new text
browser-use --session <sessionId> keys "Enter"                  # Send keyboard keys
browser-use --session <sessionId> select <index> "option"       # Select dropdown option
browser-use --session <sessionId> upload <index> <path>         # Upload file to file input
browser-use --session <sessionId> hover <index>                 # Hover over element
browser-use --session <sessionId> dblclick <index>              # Double-click element
browser-use --session <sessionId> rightclick <index>            # Right-click element

# Data Extraction
browser-use --session <sessionId> eval "js code"                # Execute JavaScript, return result
browser-use --session <sessionId> get title                     # Page title
browser-use --session <sessionId> get html [--selector "h1"]    # Page HTML, optionally scoped
browser-use --session <sessionId> get text <index>              # Element text content
browser-use --session <sessionId> get value <index>             # Input/textarea value
browser-use --session <sessionId> get attributes <index>        # Element attributes
browser-use --session <sessionId> get bbox <index>              # Bounding box

# Wait
browser-use --session <sessionId> wait selector "css"           # Wait for element
browser-use --session <sessionId> wait text "text"              # Wait for text to appear

# Cookies
browser-use --session <sessionId> cookies get [--url <url>]     # Get cookies
browser-use --session <sessionId> cookies set <name> <value>    # Set cookie
browser-use --session <sessionId> cookies clear [--url <url>]   # Clear cookies
browser-use --session <sessionId> cookies export <file>         # Export to JSON
browser-use --session <sessionId> cookies import <file>         # Import from JSON

# Session
browser-use --session <sessionId> close                         # Low-level close; prefer close_browser_session tool
browser-use sessions                                            # List active sessions
browser-use close --all                                         # Close all local Browser Use sessions
```

For advanced browser control (CDP, device emulation, tab activation), see `references/cdp-python.md`.

## Cloud API

```bash
browser-use cloud connect                 # Provision Browser Use cloud browser
browser-use cloud login <api-key>         # Save API key
browser-use cloud logout                  # Remove API key
browser-use cloud v2 GET /browsers        # REST passthrough
browser-use cloud v2 POST /tasks '{"task":"...","url":"..."}'
browser-use cloud v2 poll <task-id>       # Poll task until done
browser-use cloud v2 --help               # Show API endpoints
```

Use Browser Use cloud only when the user explicitly asks for it. The default cloud browser for this agent is Azure Playwright Service via MCP.

## Tunnels

```bash
browser-use tunnel <port>                 # Start Cloudflare tunnel
browser-use tunnel list                   # Show active tunnels
browser-use tunnel stop <port>            # Stop tunnel
browser-use tunnel stop --all             # Stop all tunnels
```

## Profile Management

```bash
browser-use profile list                  # List detected browsers and profiles
browser-use profile sync --all            # Sync profiles to cloud
browser-use profile update                # Download/update profile-use binary
```

These profile commands are primarily for local or Browser Use cloud workflows, not Azure Playwright Service remote sessions.

## Command Chaining

Commands can be chained with `&&`. The browser persists via the daemon, so chaining is safe and efficient.

```bash
browser-use --session <sessionId> open https://example.com && browser-use --session <sessionId> state
browser-use --session <sessionId> input 5 "user@example.com" && browser-use --session <sessionId> input 6 "password" && browser-use --session <sessionId> click 7
```

Chain when you do not need intermediate output. Run separately when you need to parse `state` to discover indices first.

## Common Workflows

### Azure Playwright Service Remote Browsing

1. Pick a stable session id, for example `checkout-flow`.
2. Call `create_browser_session` with `{ "sessionId": "checkout-flow" }`.
3. Connect once with a standalone command:

   ```bash
   browser-use --session checkout-flow --cdp-url "<cdpUrl>" open about:blank
   ```

4. Use normal Browser Use commands with `--session checkout-flow` and without `--cdp-url`.
5. Cleanup by calling `close_browser_session` with `{ "sessionId": "checkout-flow" }`.

### Authenticated Browsing

Azure Playwright Service remote sessions do not automatically have the user's local Chrome profile. If a task requires an authenticated site, either automate the login flow in the remote browser or ask the user whether they want a local Chrome/profile-based workflow instead.

```bash
browser-use profile list
browser-use --profile "Default" open https://github.com
```

### Exposing Local Dev Servers

```bash
browser-use tunnel 3000
browser-use --session <sessionId> open https://abc.trycloudflare.com
```

## Multiple Browsers

For subagent workflows or running multiple browsers in parallel, use `--session NAME`. Each session gets its own Browser Use daemon. For Azure Playwright Service, each Browser Use session should map to a distinct MCP `sessionId` and Playwright Service `runId`.

## Configuration

```bash
browser-use config list                            # Show all config values
browser-use config set cloud_connect_proxy jp      # Set a value
browser-use config get cloud_connect_proxy         # Get a value
browser-use config unset cloud_connect_timeout     # Remove a value
browser-use doctor                                 # Shows config + diagnostics
browser-use setup                                  # Interactive post-install setup
```

Config stored in `~/.browser-use/config.json`.

## Global Options

| Option | Description |
|--------|-------------|
| `--headed` | Show browser window |
| `--profile [NAME]` | Use real Chrome (bare `--profile` uses "Default") |
| `--cdp-url <url>` | Connect via CDP URL (`http://`, `ws://`, or `wss://`) |
| `--session NAME` | Target a named session (default: "default") |
| `--json` | Output as JSON |
| `--mcp` | Run as MCP server via stdin/stdout |

## Tips

1. **Always run `state` first** to see available elements and their indices.
2. **Use a unique `sessionId`** for each browser task.
3. **Attach to the CDP URL only once**; then reuse `--session`.
4. **Sessions persist** while the Browser Use daemon and remote browser remain alive.
5. **If commands fail**, inspect with `state` and only recreate the remote session if the daemon/browser is broken.

## Troubleshooting

- **Browser Use missing?** Run `uv pip install browser-use`.
- **`--cdp-url` unsupported?** Upgrade Browser Use: `uv pip install --upgrade browser-use`.
- **Remote browser creation times out?** Retry `create_browser_session` with a fresh session id.
- **Element not found?** `browser-use --session <sessionId> scroll down`, then `browser-use --session <sessionId> state`.
- **Run diagnostics:** `browser-use doctor`.

## Cleanup

Always call:

```json
{ "sessionId": "<sessionId>" }
```

with `close_browser_session`. It disconnects Browser Use's held WSS/CDP connection first, then ends the Playwright Service browser through MCP.
