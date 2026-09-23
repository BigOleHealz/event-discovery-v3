import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";
import type { BrowserContext, Page, Route } from "@playwright/test";

const EVENT_CACHE_NAME = "event-discovery-events-v1";

const query = new URLSearchParams({
  categories: "dedup-e2e", starts_after: "2026-09-01T00:00:00Z",
  starts_before: "2026-09-08T00:00:00Z", time_of_day_start: "18:00", time_of_day_end: "23:59",
});
const staleLabel = "Offline — showing last known events";
const abortMaps = (route: Route) => route.abort();

async function goOffline(context: BrowserContext): Promise<void> {
  // Route fulfillment bypasses offline emulation: do not replay a download offline.
  await context.route("https://maps.googleapis.com/**", abortMaps);
  await context.setOffline(true);
}

async function goOnline(context: BrowserContext): Promise<void> {
  await context.unroute("https://maps.googleapis.com/**", abortMaps);
  await context.setOffline(false);
}

async function populate(page: Page, zoom = 13): Promise<string> {
  const fixture = await readFile(new URL("./fixtures/google-maps.js", import.meta.url), "utf8");
  await page.addInitScript((zoom) => { Object.assign(window, { fixtureMapZoom: zoom }); }, zoom);
  // Deny every external request except the recorded Maps script, including on reload.
  await page.context().route("https://**/*", (route) => {
    if (route.request().url().startsWith("https://maps.googleapis.com/maps/api/js?")) {
      return route.fulfill({ status: 200, contentType: "application/javascript", body: fixture });
    }
    return route.abort();
  });
  await page.goto(`/?${query}`);
  await expect(page.getByRole("status")).toHaveText("1 event");
  await page.evaluate(async () => { await navigator.serviceWorker.ready; });
  await expect.poll(() => page.evaluate(() => navigator.serviceWorker.controller?.scriptURL)).toMatch(/\/sw\.js$/);
  const keys = await page.evaluate(async (name) => {
    const cache = await caches.open(name);
    return (await cache.keys()).map((request) => request.url);
  }, EVENT_CACHE_NAME);
  expect(keys).toHaveLength(1);
  const url = keys[0]!;
  expect(Object.fromEntries(new URL(url).searchParams)).toEqual({
    north: "40.15", south: "39.8", east: "-74.95", west: "-75.3", zoom: String(zoom),
    ...Object.fromEntries(query),
  });
  await expect(page.getByRole("alert")).toHaveCount(0);
  return url;
}

test("production shell reloads offline, cached details work, and reconnection replaces stale data", async ({ page, context }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const url = await populate(page);
  // An older public response makes network-first replacement observable without changing the DB.
  await page.evaluate(async ({ name, url }) => {
    const cache = await caches.open(name);
    const response = await cache.match(url);
    const payload = await response!.json() as { features: Array<{ properties: { title: string } }> };
    payload.features[0]!.properties.title = "Previously cached jazz show";
    await cache.put(url, new Response(JSON.stringify(payload)));
  }, { name: EVENT_CACHE_NAME, url });
  await goOffline(context);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByRole("alert")).toHaveText(staleLabel);
  await expect(page.getByRole("status")).toHaveText("1 event");
  await page.getByRole("button", { name: "Previously cached jazz show" }).click();
  const detail = page.getByRole("dialog", { name: "Previously cached jazz show" });
  await expect(detail.getByText("An evening of jazz in Philadelphia.")).toBeVisible();
  await expect(detail.locator("time")).toHaveAttribute("datetime", "2026-09-04T23:00:00Z");
  await expect(detail.getByRole("link", { name: "Register on Eventbrite" })).toHaveAttribute(
    "href", "https://www.eventbrite.com/e/jazz-show-tickets",
  );
  await expect(detail.getByRole("link", { name: "Register on Meetup" })).toHaveAttribute(
    "href", "https://www.meetup.com/jazz/events/123/",
  );
  await page.keyboard.press("Escape");
  await page.screenshot({ path: "test-results/offline-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "Previously cached jazz show" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: "test-results/offline-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1280, height: 720 });
  await goOnline(context);
  await expect(page.getByRole("alert")).toHaveCount(0, { timeout: 15_000 });
  const pin = page.locator('[data-event-marker="4e000000-0000-0000-0000-000000000001"]');
  await expect(pin).toHaveAttribute("title", "One jazz show, two sources");
  await goOffline(context);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByRole("alert")).toHaveText(staleLabel);
  await expect(page.getByRole("button", { name: "One jazz show, two sources" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("cached aggregate cells remain counts on an offline reload", async ({ page, context }) => {
  await populate(page, 12);
  await expect(page.locator("[data-event-cell]")).toHaveCount(1);
  await goOffline(context);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByRole("alert")).toHaveText(staleLabel);
  await expect(page.getByRole("status")).toHaveText("1 event");
  await expect(page.getByText(/connect and zoom in for details/)).toBeVisible();
  await expect(page.getByRole("button", { name: "One jazz show, two sources" })).toHaveCount(0);
});

test("a successful empty cache is distinguishable from an unavailable request", async ({ page, context }) => {
  await populate(page);
  await page.getByRole("group", { name: "Time of day" }).getByLabel("From", { exact: true }).fill("23:58");
  await expect(page.getByRole("status")).toHaveText("0 events");
  await goOffline(context);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByRole("status")).toHaveText("0 events");
  await expect(page.getByRole("alert")).toHaveText(staleLabel);
});

for (const [parameter, value] of [
  ["starts_after", "2026-09-02T00:00:00Z"], ["starts_before", "2026-09-07T00:00:00Z"],
  ["time_of_day_start", "19:00"], ["time_of_day_end", "23:00"], ["categories", "music"],
]) {
  test(`offline reload does not substitute cached ${parameter}`, async ({ page, context }) => {
    await populate(page);
    await goOffline(context);
    const changed = new URLSearchParams(query);
    changed.set(parameter!, value!);
    await page.goto(`/?${changed}`, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("status")).toContainText("no saved events for this viewport and filters");
    await expect(page.getByRole("status")).not.toHaveText("0 events");
    await expect(page.getByRole("button", { name: "One jazz show, two sources" })).toHaveCount(0);
    await expect(page.getByRole("alert")).toHaveCount(0);
  });
}

for (const change of ["viewport", "zoom"]) {
  test(`offline map ${change} clears old results and restores only the matching cache`, async ({ page, context }) => {
    await populate(page);
    await goOffline(context);
    await expect(page.getByRole("alert")).toHaveText(staleLabel);
    await page.evaluate((change) => {
      Object.assign(window, change === "zoom" ? { fixtureMapZoom: 12 } : { fixtureMapLatitudeShift: 1 });
      window.dispatchEvent(new Event("fixture-map-idle"));
    }, change);
    await expect(page.getByRole("status")).toContainText("no saved events");
    await expect(page.locator("[data-event-marker], [data-event-cell]")).toHaveCount(0);
    await page.evaluate(() => {
      Object.assign(window, { fixtureMapZoom: 13, fixtureMapLatitudeShift: 0 });
      window.dispatchEvent(new Event("fixture-map-idle"));
    });
    await expect(page.getByRole("status")).toHaveText("1 event");
    await expect(page.getByRole("alert")).toHaveText(staleLabel);
  });
}

test("admin requests, decisions, and bearer tokens never enter runtime caches", async ({ page }) => {
  await populate(page);
  const statuses = await page.evaluate(async (token) => {
    const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
    return Promise.all([
      fetch("/api/admin/dedup", { headers }).then((response) => response.status),
      fetch("/api/admin/dedup/00000000-0000-0000-0000-000000000000/decision", {
        method: "POST", headers, body: JSON.stringify({ decision: "skip" }),
      }).then((response) => response.status),
    ]);
  }, process.env.ADMIN_REVIEW_TOKEN!);
  expect(statuses[0]).toBe(200);
  expect(statuses[1]).toBeGreaterThanOrEqual(400);
  const contents = await page.evaluate(async () => {
    const entries: string[] = [];
    for (const name of await caches.keys()) {
      const cache = await caches.open(name);
      for (const request of await cache.keys()) {
        expectNoAuthorization(request);
        entries.push(request.url);
        if (name.startsWith("event-discovery-events-")) {
          entries.push(await (await cache.match(request))!.text());
        }
      }
    }
    function expectNoAuthorization(request: Request): void {
      if (request.headers.has("Authorization")) throw new Error("Cached authorization header");
    }
    return entries.join("\n");
  });
  expect(contents).not.toContain("/api/admin/");
  expect(contents).not.toContain(process.env.ADMIN_REVIEW_TOKEN!);
});
