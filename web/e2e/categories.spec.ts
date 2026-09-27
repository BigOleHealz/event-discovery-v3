import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";

test("parent selection includes descendants through the real category API", async ({ page }, testInfo) => {
  test.setTimeout(60_000);
  await page.addInitScript(() => {
    Object.defineProperty(window, "fixtureMapZoom", { value: 20 });
  });
  const fixture = await readFile(new URL("./fixtures/google-maps.js", import.meta.url), "utf8");
  await page.route("https://maps.googleapis.com/maps/api/js?*", (route) => route.fulfill({
    contentType: "application/javascript", body: fixture,
  }));
  // Wait for the actual response: the first Cypher query compiles on a fresh graph.
  const categoriesLoaded = page.waitForResponse((response) => response.url().endsWith("/api/categories"));
  await page.goto("/?starts_after=2050-09-01T00:00:00Z&starts_before=2050-10-01T00:00:00Z");
  expect((await categoriesLoaded).ok()).toBeTruthy();
  await expect(page.getByRole("option", { name: "Music / Jazz / Bebop", exact: true })).toBeAttached();
  const selection = page.getByRole("listbox", { name: "Categories" });
  async function selectCategory(category: string): Promise<void> {
    const [response] = await Promise.all([
      page.waitForResponse((response) => {
        const url = new URL(response.url());
        return url.pathname === "/api/events" && url.searchParams.get("categories") === category;
      }),
      selection.selectOption(category),
    ]);
    expect(response.ok()).toBeTruthy();
  }
  await selectCategory("music");
  await expect(page).toHaveURL(/categories=music/);
  await expect(page.getByRole("status")).toHaveText("2 events");
  await expect(page.locator('[data-event-marker="5b000000-0000-0000-0000-000000000001"]')).toBeVisible();
  await expect(page.locator('[data-event-marker="5b000000-0000-0000-0000-000000000002"]')).toBeVisible();
  await expect(page.locator('[data-event-marker="5b000000-0000-0000-0000-000000000003"]')).toHaveCount(0);
  await selectCategory("jazz");
  await expect(page.getByRole("status")).toHaveText("1 event");
  await page.locator('[data-event-marker="5b000000-0000-0000-0000-000000000001"]').click();
  await expect(page.getByRole("dialog", { name: "5b Bebop Evening" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("category-hierarchy.png"), fullPage: true });
  await page.reload();
  await expect(selection).toHaveValues(["jazz"]);
  await expect(page.getByRole("status")).toHaveText("1 event");
});
