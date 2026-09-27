import assert from "node:assert/strict";
import { test } from "node:test";
import { FetchFailure, type FetchRequest, type FetchResult } from "../src/browser.js";
import { createServer } from "../src/server.js";
import { config, headers } from "./helpers.js";

const result: FetchResult = {
  url: "https://fixture.invalid/events", http_status: 200, title: "Events",
  html: "<html>Rendered</html>", bytes: 21, fetch_method: "stagehand",
};

test("health is cheap; authenticated fetch forwards the validated request and returns JSON", async (t) => {
  const calls: FetchRequest[] = [];
  const app = createServer(config, async (request) => { calls.push(request); return result; });
  t.after(() => app.close());
  assert.deepEqual((await app.inject("/health")).json(), { status: "ok" });
  assert.equal(calls.length, 0);
  const payload = { url: result.url, ready_selector: "article" };
  const response = await app.inject({ method: "POST", url: "/v1/fetch", headers, payload });
  assert.equal(response.statusCode, 200);
  assert.deepEqual(response.json(), result);
  assert.deepEqual(calls, [payload]);
});

test("unauthenticated, malformed, non-HTTP, credential-bearing and oversized requests never launch a browser", async (t) => {
  let calls = 0;
  const app = createServer(config, async () => { calls += 1; return result; });
  t.after(() => app.close());
  for (const authorization of [undefined, "Bearer wrong", `Bearer ${"x".repeat(config.apiToken.length)}`]) {
    const response = await app.inject({ method: "POST", url: "/v1/fetch", headers: authorization ? { authorization } : {}, payload: { url: result.url } });
    assert.equal(response.statusCode, 401);
  }
  for (const payload of [{}, { url: "file:///etc/passwd" }, { url: "javascript:alert(1)" }, { url: "https://user:secret@example.com" }, { url: result.url, script: "arbitrary code" }, { url: result.url, ready_selector: "" }]) {
    assert.equal((await app.inject({ method: "POST", url: "/v1/fetch", headers, payload })).statusCode, 400);
  }
  const malformed = await app.inject({ method: "POST", url: "/v1/fetch", headers: { ...headers, "content-type": "application/json" }, payload: "{" });
  assert.equal(malformed.statusCode, 400);
  const oversized = await app.inject({ method: "POST", url: "/v1/fetch", headers, payload: { url: "x".repeat(17_000) } });
  assert.equal(oversized.statusCode, 413);
  assert.equal(calls, 0);
});

test("capacity is bounded and recovered after failure without exposing browser errors", async (t) => {
  let rejectFetch: (error: Error) => void = () => { throw new Error("not started"); };
  let started: () => void = () => {};
  const ready = new Promise<void>((resolve) => { started = resolve; });
  let calls = 0;
  const app = createServer(config, () => {
    calls += 1;
    if (calls > 1) throw new FetchFailure("fetch_timeout");
    return new Promise<FetchResult>((_resolve, reject) => { rejectFetch = reject; started(); });
  });
  t.after(() => app.close());
  const request = { method: "POST" as const, url: "/v1/fetch", headers, payload: { url: result.url } };
  const first = app.inject(request).then((response) => response);
  await ready;
  const busy = await app.inject(request);
  assert.equal(busy.statusCode, 503);
  assert.equal(busy.headers["retry-after"], "1");
  rejectFetch(new Error("SECRET target and credentials"));
  assert.deepEqual((await first).json(), { error: "browser_failure" });
  assert.equal((await app.inject(request)).statusCode, 504);
});

test("page size and browser failures have stable gateway error responses", async (t) => {
  const app = createServer(config, async () => { throw new FetchFailure("page_too_large"); });
  t.after(() => app.close());
  const response = await app.inject({ method: "POST", url: "/v1/fetch", headers, payload: { url: result.url } });
  assert.equal(response.statusCode, 502);
  assert.deepEqual(response.json(), { error: "page_too_large" });
});
