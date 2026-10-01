import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";

test("Google sign-in persists a user, survives reload, refreshes, and logs out", async ({ page, context }, testInfo) => {
  const api = process.env.AUTH_E2E_API_URL;
  test.skip(!api, "Run from api/: python tests/run_auth_browser.py");
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const map = await readFile(new URL("./fixtures/google-maps.js", import.meta.url), "utf8");
  await page.route("https://maps.googleapis.com/maps/api/js?*", (route) => route.fulfill({
    contentType: "application/javascript", body: map,
  }));
  // Category projection is unrelated to sign-in; auth and event requests reach the real API.
  await page.route("**/api/categories", (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  await expect(page.getByRole("link", { name: "Sign in with Google" })).toBeVisible();
  await page.getByRole("link", { name: "Sign in with Google" }).click();
  await expect(page.getByText("Signed in as Test Friend")).toBeVisible();
  const before = await page.evaluate(async (url) => {
    const response = await fetch(`${url}/api/me`, { credentials: "include" });
    return { status: response.status, cache: response.headers.get("cache-control"), user: await response.json() as { id: string } };
  }, api);
  expect(before.status).toBe(200);
  expect(before.cache).toBe("no-store");
  const session = (await context.cookies()).find((cookie) => cookie.name === "event_session");
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe("Lax");
  expect(await page.evaluate(() => document.cookie)).not.toContain("event_session");
  await page.reload();
  await expect(page.getByText("Signed in as Test Friend")).toBeVisible();
  expect(await page.evaluate(async (url) => (await fetch(`${url}/api/auth/refresh`, {
    method: "POST", credentials: "include",
  })).status, api)).toBe(200);
  await expect(page.getByText("Loading Philadelphia events…")).not.toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("auth-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: testInfo.outputPath("auth-mobile.png") });
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("link", { name: "Sign in with Google" })).toBeVisible();
  expect(await page.evaluate(async (url) => (await fetch(`${url}/api/me`, {
    credentials: "include",
  })).status, api)).toBe(401);
  await page.getByRole("link", { name: "Sign in with Google" }).click();
  await expect(page.getByText("Signed in as Test Friend")).toBeVisible();
  const after = await page.evaluate(async (url) => (await fetch(`${url}/api/me`, {
    credentials: "include",
  })).json() as Promise<{ id: string }>, api);
  expect(after.id).toBe(before.user.id);
  const cached = await page.evaluate(async () => {
    const requests: string[] = [];
    for (const name of await caches.keys()) {
      requests.push(...(await (await caches.open(name)).keys()).map((request) => request.url));
    }
    return requests;
  });
  expect(cached.some((url) => url.includes("/api/me") || url.includes("/api/auth/"))).toBe(false);
  expect(errors).toEqual([]);
});
