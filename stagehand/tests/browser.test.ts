import assert from "node:assert/strict";
import { test } from "node:test";
import { createFetcher, FetchFailure, type LaunchBrowser } from "../src/browser.js";
import { loadConfig } from "../src/config.js";
import { config } from "./helpers.js";

test("invalid environment fails without leaking values", () => {
  assert.throws(() => loadConfig({ STAGEHAND_API_TOKEN: "secret" }), (error: unknown) => {
    assert.ok(error instanceof Error);
    assert.match(error.message, /STAGEHAND_API_TOKEN/);
    assert.doesNotMatch(error.message, /secret/);
    return true;
  });
});

test("browser closes when page creation fails", async () => {
  let closed = 0;
  const fetch = createFetcher(config, async () => ({
    context: { newPage: async () => { throw new Error("page crashed"); } },
    close: async () => { closed += 1; },
  }));
  await assert.rejects(fetch({ url: "http://fixture.invalid" }), new FetchFailure("browser_failure"));
  assert.equal(closed, 1);
});

test("a launch resolving after the deadline is closed without navigating", async () => {
  let closed = 0;
  let pages = 0;
  const launch: LaunchBrowser = async () => {
    await new Promise((resolve) => setTimeout(resolve, 25));
    return {
      context: { newPage: async () => { pages += 1; throw new Error("must not navigate"); } },
      close: async () => { closed += 1; },
    };
  };
  await assert.rejects(createFetcher({ ...config, fetchTimeoutMs: 5 }, launch)({ url: "http://fixture.invalid" }), new FetchFailure("fetch_timeout"));
  assert.equal(closed, 1);
  assert.equal(pages, 0);
});
