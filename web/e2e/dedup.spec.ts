import { readFile } from "node:fs/promises";

import { expect, test } from "@playwright/test";

test("one canonical pin offers Eventbrite and Meetup registration", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    Object.defineProperty(window, "fixtureMapZoom", { value: 13 });
  });
  const mapsFixture = await readFile(new URL("./fixtures/google-maps.js", import.meta.url), "utf8");
  await page.route("https://maps.googleapis.com/maps/api/js?*", (route) => route.fulfill({
    status: 200, contentType: "application/javascript", body: mapsFixture,
  }));
  const query = new URLSearchParams({
    categories: "dedup-e2e",
    starts_after: "2026-09-01T00:00:00Z",
    starts_before: "2026-09-08T00:00:00Z",
  });
  // Event data is served by the real API and Postgres; only Maps is replayed.
  const response = await page.request.get(`/api/events?${query}&zoom=13`);
  expect(response.ok()).toBe(true);
  const payload = await response.json() as {
    features: Array<{ properties: { registration_links: Array<{ source: string; url: string }> } }>;
  };
  expect(payload.features).toHaveLength(1);
  expect(payload.features[0]?.properties.registration_links).toHaveLength(2);

  await page.goto(`/?${query}`);
  await expect(page.getByRole("status")).toHaveText("1 event");
  const pin = page.locator('[data-event-marker="4e000000-0000-0000-0000-000000000001"]');
  await expect(pin).toHaveCount(1);
  await pin.click();
  const detail = page.getByRole("dialog", { name: "One jazz show, two sources" });
  await expect(detail.getByRole("link")).toHaveCount(2);
  await expect(detail.getByRole("link", { name: "Register on Eventbrite" })).toHaveAttribute(
    "href", "https://www.eventbrite.com/e/jazz-show-tickets",
  );
  await expect(detail.getByRole("link", { name: "Register on Meetup" })).toHaveAttribute(
    "href", "https://www.meetup.com/jazz/events/123/",
  );
  await expect(detail.getByText("Choose where to register for this event.")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(detail).toBeHidden();
  expect(errors).toEqual([]);
});
