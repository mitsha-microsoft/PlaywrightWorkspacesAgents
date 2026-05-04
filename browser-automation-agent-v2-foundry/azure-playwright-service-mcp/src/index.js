#!/usr/bin/env node

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import WebSocket from "ws";
import { z } from "zod";

const DEFAULT_TIMEOUT_MS = 90_000;

function parseArgs(args) {
  const options = {};

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];

    switch (arg) {
      case "--help":
      case "-h":
        options.help = true;
        break;
      case "--service-url":
      case "--serviceUrl":
        options.serviceUrl = readValue(args, ++index, arg);
        break;
      case "--access-token":
      case "--accessToken":
        options.accessToken = readValue(args, ++index, arg);
        break;
      case "--request-timeout-ms":
        options.requestTimeoutMs = Number(readValue(args, ++index, arg));
        break;
      default:
        throw new Error(`Unknown option: ${arg}`);
    }
  }

  return options;
}

function readValue(args, index, optionName) {
  const value = args[index];
  if (!value || value.startsWith("--")) {
    throw new Error(`${optionName} requires a value`);
  }
  return value;
}

function printUsage() {
  process.stdout.write(`Usage: azure-playwright-service-mcp [options]

Options:
  --service-url <url>         Playwright Service WSS endpoint.
  --access-token <token>      Access token sent as a Bearer token.
  --request-timeout-ms <ms>   Browser provisioning timeout. Defaults to 90000.
  --help                      Show this help text.

Environment variables:
  AZURE_PLAYWRIGHT_SERVICE_URL or PLAYWRIGHT_SERVICE_URL
  AZURE_PLAYWRIGHT_SERVICE_ACCESS_TOKEN or PLAYWRIGHT_SERVICE_ACCESS_TOKEN
`);
}

function readConfig() {
  const options = parseArgs(process.argv.slice(2));

  if (options.help) {
    printUsage();
    process.exit(0);
  }

  const serviceUrl =
    options.serviceUrl ||
    process.env.AZURE_PLAYWRIGHT_SERVICE_URL ||
    process.env.PLAYWRIGHT_SERVICE_URL;
  const accessToken =
    options.accessToken ||
    process.env.AZURE_PLAYWRIGHT_SERVICE_ACCESS_TOKEN ||
    process.env.PLAYWRIGHT_SERVICE_ACCESS_TOKEN;
  const requestTimeoutMs = options.requestTimeoutMs || DEFAULT_TIMEOUT_MS;

  if (!serviceUrl) {
    throw new Error(
      "Missing Playwright Service URL. Pass --service-url or set AZURE_PLAYWRIGHT_SERVICE_URL.",
    );
  }

  if (!accessToken) {
    throw new Error(
      "Missing Playwright Service access token. Pass --access-token or set AZURE_PLAYWRIGHT_SERVICE_ACCESS_TOKEN.",
    );
  }

  if (!Number.isInteger(requestTimeoutMs) || requestTimeoutMs <= 0) {
    throw new Error("--request-timeout-ms must be a positive integer.");
  }

  const provisioningUrl = buildProvisioningUrl(serviceUrl);

  return {
    serviceUrl,
    provisioningUrl,
    accessToken,
    requestTimeoutMs,
  };
}

function buildProvisioningUrl(serviceUrl) {
  let parsed;
  try {
    parsed = new URL(serviceUrl);
  } catch (error) {
    throw new Error(`Invalid Playwright Service URL: ${error.message}`);
  }

  if (parsed.protocol !== "wss:") {
    throw new Error("Playwright Service URL must use the wss:// protocol.");
  }

  const match = parsed.pathname.match(
    /^\/playwrightworkspaces\/([^/]+)\/browsers\/?$/i,
  );
  if (!match) {
    throw new Error(
      "Playwright Service URL must match wss://<region>.api.playwright.microsoft.com/playwrightworkspaces/{workspaceId}/browsers.",
    );
  }

  const provisioningUrl = new URL(parsed.toString());
  provisioningUrl.protocol = "https:";
  provisioningUrl.search = "";
  provisioningUrl.hash = "";
  provisioningUrl.searchParams.set("os", "linux");
  provisioningUrl.searchParams.set("browser", "chromium");
  provisioningUrl.searchParams.set("playwrightVersion", "cdp");
  provisioningUrl.searchParams.set("shouldRedirect", "false");

  return {
    url: provisioningUrl,
    workspaceId: match[1],
  };
}

function buildSessionRequestUrl(config, sessionId) {
  const url = new URL(config.provisioningUrl.url);
  url.searchParams.set("runId", sessionId);
  return url;
}

async function getBrowserSessionUrl(config, sessionId) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), config.requestTimeoutMs);

  try {
    const response = await fetch(buildSessionRequestUrl(config, sessionId), {
      method: "GET",
      headers: {
        Authorization: `Bearer ${config.accessToken}`,
        Accept: "application/json",
      },
      signal: controller.signal,
    });

    if (!response.ok) {
      const body = await response.text();
      throw new Error(
        `Playwright Service returned HTTP ${response.status} ${response.statusText}: ${body}`,
      );
    }

    const body = await response.json();
    if (!body || typeof body.sessionUrl !== "string") {
      throw new Error("Playwright Service response did not include a string sessionUrl.");
    }

    const sessionUrl = new URL(body.sessionUrl);
    if (sessionUrl.protocol !== "wss:") {
      throw new Error("Playwright Service sessionUrl must use the wss:// protocol.");
    }

    return sessionUrl.toString();
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error(
        `Timed out after ${config.requestTimeoutMs}ms while waiting for the remote browser to start.`,
      );
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

async function closeBrowserSession(sessionUrl) {
  await new Promise((resolve, reject) => {
    const socket = new WebSocket(sessionUrl);
    const timeout = setTimeout(() => {
      socket.close();
      reject(new Error("Timed out while closing the remote browser session."));
    }, 10_000);

    let settled = false;

    function finish(error) {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timeout);
      if (error) {
        reject(error);
      } else {
        resolve();
      }
    }

    socket.once("open", () => {
      socket.send(JSON.stringify({ id: 1, method: "Browser.close" }), (error) => {
        if (error) {
          finish(error);
        }
      });
    });

    socket.on("message", (data) => {
      try {
        const message = JSON.parse(data.toString());
        if (message.id === 1 && message.error) {
          finish(new Error(`Browser.close failed: ${JSON.stringify(message.error)}`));
        }
        if (message.id === 1) {
          socket.close();
          finish();
        }
      } catch (error) {
        finish(new Error(`Invalid CDP response while closing browser: ${error.message}`));
      }
    });

    socket.once("close", () => finish());
    socket.once("error", (error) => finish(error));
  });
}

function jsonContent(value) {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(value, null, 2),
      },
    ],
  };
}

async function main() {
  const config = readConfig();
  const server = new McpServer({
    name: "azure-playwright-service-mcp",
    version: "0.1.0",
  });

  server.registerTool(
    "create_browser_session",
    {
      title: "Create browser session",
      description:
        "Creates a Microsoft Playwright Workspaces Chromium browser session for the provided runId and returns its CDP WebSocket URL.",
      inputSchema: {
        sessionId: z.string().min(1).describe("Caller-defined session identifier."),
      },
    },
    async ({ sessionId }) => {
      const cdpUrl = await getBrowserSessionUrl(config, sessionId);

      return jsonContent({
        sessionId,
        cdpUrl,
      });
    },
  );

  server.registerTool(
    "end_browser_session",
    {
      title: "End browser session",
      description:
        "Resolves the remote browser session for the provided runId and closes it.",
      inputSchema: {
        sessionId: z.string().min(1).describe("Session identifier passed to create_browser_session."),
      },
    },
    async ({ sessionId }) => {
      const cdpUrl = await getBrowserSessionUrl(config, sessionId);
      await closeBrowserSession(cdpUrl);

      return jsonContent({
        sessionId,
        cdpUrl,
        ended: true,
      });
    },
  );

  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exit(1);
});
