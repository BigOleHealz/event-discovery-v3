import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";

test("similar events use projected scores and open details beyond the current filter", async ({ page }, testInfo) => {
  test.setTimeout(60_000);
  await page.addInitScript(() => {
    Object.defineProperty(window, "fixtureMapZoom", { value: 20 });
  });
  const fixture = await readFile(new URL("./fixtures/google-maps.js", import.meta.url), "utf8");
  await page.route("https://maps.googleapis.com/maps/api/js?*", (route) => route.fulfill({
    contentType: "application/javascript", body: fixture,
  }));
  await page.goto("/?categories=jazz&starts_after=2050-09-01T00:00:00Z&starts_before=2050-10-01T00:00:00Z");
  await page.locator('[data-event-marker="5b000000-0000-0000-0000-000000000001"]').click();
  const recommendations = page.getByRole("region", { name: "Similar events" });
  await expect(recommendations.getByRole("button", { name: /5b Rock Evening/ })).toBeVisible();
  await expect(recommendations.getByRole("button")).toHaveCount(1);
  await recommendations.getByRole("button", { name: /5b Rock Evening/ }).click();
  await expect(page.getByRole("dialog", { name: "5b Rock Evening" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Register on Fixture" }))
    .toHaveAttribute("href", "https://fixture.test/register/2");
  await expect(recommendations.getByRole("button", { name: /5b Bebop Evening/ })).toBeVisible();
  await expect(page.getByRole("listbox", { name: "Categories" })).toHaveValues(["jazz"]);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await recommendations.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("similar-events.png"), fullPage: true });
  await page.getByRole("button", { name: "Close details" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
