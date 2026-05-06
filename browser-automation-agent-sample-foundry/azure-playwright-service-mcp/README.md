# Azure Playwright Service MCP Server

An MCP server that provisions remote Chromium sessions from Microsoft Playwright Workspaces and returns CDP WebSocket URLs to MCP clients.

## Tools

| Tool | Input | Result |
| --- | --- | --- |
| `create_browser_session` | `{ "sessionId": "my-session" }` | Creates a remote browser and returns `{ "sessionId": "...", "cdpUrl": "wss://..." }`. |
| `end_browser_session` | `{ "sessionId": "my-session" }` | Resolves and closes the remote browser for that session id. |

The server is stateless. It passes the tool `sessionId` to Playwright Service as the `runId` query parameter for both creating and ending sessions.

## Configuration

The server needs:

1. A Playwright Service WebSocket endpoint in this format:

   ```text
   wss://<region>.api.playwright.microsoft.com/playwrightworkspaces/<workspaceId>/browsers
   ```

2. A Playwright Service access token.

You can provide both values with command-line flags:

```powershell
node src\index.js --service-url "wss://<region>.api.playwright.microsoft.com/playwrightworkspaces/<workspaceId>/browsers" --access-token "<token>"
```

Or with environment variables:

```powershell
$env:AZURE_PLAYWRIGHT_SERVICE_URL = "wss://<region>.api.playwright.microsoft.com/playwrightworkspaces/<workspaceId>/browsers"
$env:AZURE_PLAYWRIGHT_SERVICE_ACCESS_TOKEN = "<token>"
npm start
```

The aliases `PLAYWRIGHT_SERVICE_URL` and `PLAYWRIGHT_SERVICE_ACCESS_TOKEN` are also supported.

## How session creation works

When `create_browser_session` is called, the server converts the configured WSS endpoint:

```text
wss://<region>.api.playwright.microsoft.com/playwrightworkspaces/<workspaceId>/browsers
```

into:

```text
https://<region>.api.playwright.microsoft.com/playwrightworkspaces/<workspaceId>/browsers?os=linux&browser=chromium&playwrightVersion=cdp&shouldRedirect=false&runId=<sessionId>
```

It then sends an HTTP `GET` request with:

```http
Authorization: Bearer <token>
Accept: application/json
```

The request waits up to 90 seconds by default for the browser to start, parses the JSON response, and returns the `sessionUrl` field as the CDP URL.

`end_browser_session` makes the same authenticated HTTPS request with the same `runId`, extracts `sessionUrl`, connects to that CDP WebSocket URL, and sends `Browser.close`.

## Install and run

```powershell
cd azure-playwright-service-mcp
npm install
npm start -- --service-url "wss://<region>.api.playwright.microsoft.com/playwrightworkspaces/<workspaceId>/browsers" --access-token "<token>"
```

Optional timeout override:

```powershell
npm start -- --request-timeout-ms 60000
```

## MCP client configuration

Example MCP client config:

```json
{
  "mcpServers": {
    "azure-playwright-service": {
      "command": "node",
      "args": [
        "D:\\Work\\PlaywrightSampleMcp\\azure-playwright-service-mcp\\src\\index.js",
        "--service-url",
        "wss://<region>.api.playwright.microsoft.com/playwrightworkspaces/<workspaceId>/browsers",
        "--access-token",
        "<token>"
      ]
    }
  }
}
```

Prefer environment variables or your MCP client's secret management support for the access token when available.

## Development

```powershell
npm install
npm run check
```
