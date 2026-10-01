import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";

test("past attendance prompts a rating and text that survive reload", async ({ page }, testInfo) => {
  const api = process.env.AUTH_E2E_API_URL;
  test.skip(!api, "Run from api/: python tests/run_auth_browser.py");
  const map = await readFile(new URL("./fixtures/google-maps.js", import.meta.url), "utf8");
  await page.route("https://maps.googleapis.com/maps/api/js?*", (route) => route.fulfill({
    contentType: "application/javascript", body: map,
  }));
  await page.route("**/api/categories", (route) => route.fulfill({ json: [] }));
  await page.goto(`${api}/fixture/identity/feedback`);
  await page.getByRole("link", { name: "Sign in with Google", exact: true }).click();
  await expect(page.getByText("Signed in as Feedback")).toBeVisible();
  const queued = await page.request.post(`${api}/fixture/finish-feedback-event`);
  expect(await queued.json()).toEqual({ attended: 1, requested: 1 });
  await page.getByRole("button", { name: "Event feedback", exact: true }).click();
  const panel = page.getByRole("dialog", { name: "Event feedback" });
  await expect(panel.getByText("Yesterday’s show", { exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Save feedback" })).toBeDisabled();
  await panel.getByRole("radio", { name: "4 stars", exact: true }).check();
  await panel.getByLabel("Tell us more (optional)").fill("Loved the music.");
  await panel.getByRole("button", { name: "Save feedback" }).click();
  await expect(panel.getByText("Feedback saved. Thank you!")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("feedback-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath("feedback-mobile.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.reload();
  await page.getByRole("button", { name: "Event feedback", exact: true }).click();
  await expect(panel.getByText("No events here yet.")).toBeVisible();
  await panel.getByRole("button", { name: "Reviewed", exact: true }).click();
  await expect(panel.getByRole("radio", { name: "4 stars", exact: true })).toBeChecked();
  await expect(panel.getByLabel("Tell us more (optional)")).toHaveValue("Loved the music.");
  const cached = await page.evaluate(async () => {
    const requests: string[] = [];
    for (const name of await caches.keys()) {
      requests.push(...(await (await caches.open(name)).keys()).map((request) => request.url));
    }
    return requests;
  });
  expect(cached.some((url) => url.includes("/api/attendance"))).toBe(false);
});
