import { expect, test } from "@playwright/test";

test("admin reviews real pairs: merge, distinct, skip, and persisted decisions", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const token = process.env.ADMIN_REVIEW_TOKEN;
  if (!token) throw new Error("ADMIN_REVIEW_TOKEN is required for the review E2E fixture");
  expect((await page.request.get("/api/admin/dedup")).status()).toBe(401);
  await page.goto("/admin/dedup");
  await expect(page.getByRole("heading", { name: "Dedup review", exact: true })).toBeVisible();
  await page.getByLabel("Admin token").fill(token);
  await page.getByRole("button", { name: "Unlock", exact: true }).click();
  await expect(page.getByText("3 pending pairs")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Review eventbrite 1" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Review meetup 1" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open eventbrite listing" })).toHaveAttribute(
    "href", "https://eventbrite.test/review/1",
  );
  await expect(page.getByText("Original meetup description 1")).toBeVisible();
  await expect(page.getByText("Review Jazz Room", { exact: true })).toHaveCount(2);
  const cards = page.getByRole("region", { name: "Event comparison" }).getByRole("article");
  const left = await cards.nth(0).boundingBox();
  const right = await cards.nth(1).boundingBox();
  expect(left?.y).toBe(right?.y);
  expect(right!.x).toBeGreaterThan(left!.x);
  await page.screenshot({ path: "test-results/review-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  const mobileFirst = await cards.nth(0).boundingBox();
  const mobileSecond = await cards.nth(1).boundingBox();
  expect(mobileSecond!.y).toBeGreaterThan(mobileFirst!.y);
  await page.screenshot({ path: "test-results/review-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1280, height: 720 });

  // Real API + PostGIS throughout; a merge reduces map pins and adds both source links.
  const params = new URLSearchParams({
    zoom: "13", categories: "review-e2e", starts_after: "2026-10-01T00:00:00Z",
    starts_before: "2026-10-15T00:00:00Z",
  });
  const before = await (await page.request.get(`/api/events?${params}`)).json() as { features: unknown[] };
  expect(before.features).toHaveLength(6);
  await page.getByRole("button", { name: "Merge (M)" }).click();
  await expect(page.getByText("2 pending pairs")).toBeVisible();
  const after = await (await page.request.get(`/api/events?${params}`)).json() as {
    features: Array<{ properties: { title: string; registration_links: Array<{ source: string }> } }>;
  };
  expect(after.features).toHaveLength(5);
  const merged = after.features.find((feature) => feature.properties.title === "Review eventbrite 1");
  expect(merged?.properties.registration_links.map((link) => link.source).sort()).toEqual(["eventbrite", "meetup"]);
  await page.keyboard.press("d");
  await expect(page.getByText("1 pending pairs")).toBeVisible();
  await page.keyboard.press("s");
  await expect(page.getByText(/all caught up/)).toBeVisible();
  const final = await (await page.request.get(`/api/events?${params}`)).json() as { features: unknown[] };
  expect(final.features).toHaveLength(5);
  await page.reload();
  await expect(page.getByLabel("Admin token")).toHaveValue("");
  await page.getByLabel("Admin token").fill(token);
  await page.getByRole("button", { name: "Unlock", exact: true }).click();
  await expect(page.getByText(/all caught up/)).toBeVisible();
  expect(errors).toEqual([]);
});
