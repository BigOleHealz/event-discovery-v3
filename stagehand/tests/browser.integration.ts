import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { test } from "node:test";
import { browserLauncher, createFetcher, type FetchResult } from "../src/browser.js";
import { createServer } from "../src/server.js";
import { config, headers } from "./helpers.js";

test("real Stagehand renders JS through HTTP, isolates profiles, and cleans up failures", { timeout: 180_000 }, async (t) => {
  // Entirely synthetic site, served on loopback. No model keys or public network needed.
  const fixture = createHttpServer((request, response) => {
    if (request.url === "/redirect") {
      response.writeHead(302, { location: "/events" }).end();
      return;
    }
    if (request.url === "/hang") return;
    response.writeHead(request.url === "/missing" ? 404 : 200, { "content-type": "text/html" });
    response.end(`<!doctype html><html><head><title>Fixture events</title></head><body>
      <main id="events"></main><button class="next" onclick="document.querySelector('main').dataset.page='2'">Next</button><script>
      const previous = localStorage.getItem("visited");
      localStorage.setItem("visited", "yes");
      setTimeout(() => {
        const event = document.createElement("article");
        event.textContent = previous ? "PROFILE LEAK" : "Local concert — 2026-10-01 — 123 Main Street";
        document.querySelector("main").append(event);
      }, 50);
      </script></body></html>`);
  });
  await new Promise<void>((resolve) => fixture.listen(0, "127.0.0.1", resolve));
  t.after(() => { fixture.closeAllConnections(); fixture.close(); });
  const address = fixture.address();
  assert.ok(address && typeof address !== "string");
  const base = `http://127.0.0.1:${address.port}`;
  const raw = await (await fetch(`${base}/events`)).text();
  assert.doesNotMatch(raw, /<article>/);

  let launched = 0;
  let closed = 0;
  const launch = async () => {
    const browser = await browserLauncher(config)();
    launched += 1;
    return { context: browser.context, close: async () => { await browser.close(); closed += 1; } };
  };
  const app = createServer(config, createFetcher(config, launch));
  t.after(() => app.close());
  const service = await app.listen({ host: "127.0.0.1", port: 0 });
  for (const path of ["/redirect", "/events", "/missing"]) {
    const response = await fetch(`${service}/v1/fetch`, {
      method: "POST", headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({ url: `${base}${path}`, ready_selector: "article" }),
    });
    assert.equal(response.status, 200, await response.clone().text());
    const page = await response.json() as FetchResult;
    assert.match(page.html, /<article>Local concert/);
    assert.doesNotMatch(page.html, /<article>PROFILE LEAK/);
    assert.equal(page.url, `${base}${path === "/redirect" ? "/events" : path}`);
    assert.equal(page.http_status, path === "/missing" ? 404 : 200);
    assert.equal(page.bytes, Buffer.byteLength(page.html));
    assert.equal(page.title, "Fixture events");
    assert.equal(closed, launched);
  }

  await assert.rejects(createFetcher({ ...config, maxHtmlBytes: 10 }, launch)({ url: `${base}/events` }), { message: "page_too_large" });
  assert.equal(closed, launched);
  // Allow Chromium to launch before exercising a navigation deadline.
  await assert.rejects(createFetcher({ ...config, fetchTimeoutMs: 5000 }, launch)({ url: `${base}/hang` }), { message: "fetch_timeout" });
  assert.equal(closed, launched);
  const advanced = await createFetcher(config, launch)({
    url: `${base}/events`, ready_selector: "article",
    steps: [{ selector: "button.next", wait_for_selector: "main[data-page='2']", delay_ms: 1 }],
  });
  assert.match(advanced.html, /data-page="2"/);
  assert.equal(closed, launched);
  assert.equal(launched, 6);
});
