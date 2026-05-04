---
name: azure-playwright-browser-automation
description: Automates browser interactions for web testing, form filling, screenshots, and data extraction using Browser Use CLI connected to a remote Azure Playwright Service browser through MCP. Use when the user needs to navigate websites, interact with web pages, fill forms, take screenshots, test web applications, or extract information from web pages.
allowed-tools: run_shell, create_browser_session, close_browser_session
---

# Browser Automation with browser-use CLI and Azure Playwright Service

The `browser-use` command provides fast, persistent browser automation. A background daemon keeps the browser open across commands, giving low latency per call.

This hosted agent uses Azure Playwright Service for the browser itself. Do not launch a local Browser Use browser for automation tasks unless the user explicitly asks for local browsing. Instead:

1. Call the `create_browser_session` MCP tool with a stable `sessionId`.
2. Read the returned `cdpUrl`.
3. Use `run_shell` to connect Browser Use to that CDP URL once. This first command is only a connection handshake and must open `about:blank`:

   ```bash
   browser-use --session <sessionId> --cdp-url "<cdpUrl>" open about:blank
   ```

   Do not combine this command with navigation, `eval`, `state`, `&&`, `;`, pipes, or any other browser operation. Do not open the target website in the same command that passes `--cdp-url`.
   Do not add shell-specific environment-variable setup such as `PYTHONIOENCODING=...`; the `run_shell` tool already sets UTF-8 output handling.

4. After the `about:blank` connection command succeeds, run the target browser work in separate commands that reuse the same Browser Use session without `--cdp-url`:

   ```bash
   browser-use --session <sessionId> state
   browser-use --session <sessionId> open https://example.com
   ```

5. When the browser task is complete, call the `close_browser_session` tool with the same `sessionId`.

   Do not call `end_browser_session` directly. `close_browser_session` first runs `browser-use --session <sessionId> close` to disconnect Browser Use's held WSS/CDP connection, then calls MCP `end_browser_session`.

Keep the Browser Use daemon alive across turns. Do not repeatedly reconnect to the same Azure Playwright Service CDP URL; connect once to `about:blank`, then reuse the Browser Use session.

## Installation

The hosted container installs Browser Use at build time. If running locally outside the container, verify Browser Use is installed:

```bash
browser-use doctor
```

Manual installation:

```bash
uv pip install browser-use
```

The CLI must support the `--cdp-url` global option. If `browser-use --help` does not show `--cdp-url`, install or upgrade Browser Use before trying to connect to the remote browser.

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

## Commands

```bash
# Navigation
browser-use --session <sessionId> open <url>
browser-use --session <sessionId> back
browser-use --session <sessionId> scroll down
browser-use --session <sessionId> scroll up
browser-use --session <sessionId> tab list
browser-use --session <sessionId> tab new [url]
browser-use --session <sessionId> tab switch <index>
browser-use --session <sessionId> tab close <index> [index...]

# Page State - always run state first to get element indices
browser-use --session <sessionId> state
browser-use --session <sessionId> screenshot [path.png]

# Interactions - use indices from state
browser-use --session <sessionId> click <index>
browser-use --session <sessionId> click <x> <y>
browser-use --session <sessionId> type "text"
browser-use --session <sessionId> input <index> "text"
browser-use --session <sessionId> input <index> ""
browser-use --session <sessionId> keys "Enter"
browser-use --session <sessionId> select <index> "option"
browser-use --session <sessionId> upload <index> <path>
browser-use --session <sessionId> hover <index>
browser-use --session <sessionId> dblclick <index>
browser-use --session <sessionId> rightclick <index>

# Data Extraction
browser-use --session <sessionId> eval "js code"
browser-use --session <sessionId> get title
browser-use --session <sessionId> get html [--selector "h1"]
browser-use --session <sessionId> get text <index>
browser-use --session <sessionId> get value <index>
browser-use --session <sessionId> get attributes <index>
browser-use --session <sessionId> get bbox <index>

# Wait
browser-use --session <sessionId> wait selector "css"
browser-use --session <sessionId> wait text "text"

# Session
browser-use --session <sessionId> close
browser-use sessions
browser-use close --all
```

## Cleanup

Always call:

```json
{ "sessionId": "<sessionId>" }
```

with `close_browser_session`. It disconnects Browser Use's held WSS/CDP connection first, then ends the Playwright Service browser through MCP.
