import type { Config } from "../src/config.js";

export const config: Config = {
  host: "127.0.0.1",
  port: 8081,
  apiToken: "test-only-stagehand-token-123456",
  executablePath: process.env.STAGEHAND_BROWSER_EXECUTABLE ?? "/usr/bin/chromium",
  browserSandbox: false,
  fetchTimeoutMs: 20_000,
  maxConcurrency: 1,
  maxHtmlBytes: 50_000,
};

export const headers = { authorization: `Bearer ${config.apiToken}` };
