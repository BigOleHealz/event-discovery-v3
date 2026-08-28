import { readFile } from "node:fs/promises";

import { expect, test } from "@playwright/test";

const mapsFixturePath = new URL("./fixtures/google-maps.js", import.meta.url);

test("renders pins, event detail, and an installable offline shell", async ({ page, context }) => {
  const mapsFixture = await readFile(mapsFixturePath, "utf8");
  await page.route("https://maps.googleapis.com/maps/api/js?*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/javascript",
      body: mapsFixture,
    });
  });
  await page.route("**/api/events", async (route) => {
    const response = await route.fetch();
    const payload = (await response.json()) as {
      features: Array<{
        properties: {
          registration_links: Array<{ source: string; url: string }>;
        };
      }>;
    };
    const firstFeature = payload.features[0];
    if (firstFeature === undefined) {
      throw new Error("Event fixture response contains no features");
    }
    firstFeature.properties.registration_links = [
      {
        source: "eventbrite",
        url: "https://www.eventbrite.com/e/parkway-jazz-night",
      },
    ];
    await route.fulfill({ response, json: payload });
  });

  await page.goto("/");

  await expect(page.getByRole("status")).toHaveText("20 events");
  await expect(page.getByTestId("event-map")).toHaveAttribute("data-google-map-ready", "true");
  await expect(page.locator("[data-event-marker]")).toHaveCount(20);

  await page.locator("[data-event-marker]").first().click();
  const detail = page.getByRole("dialog");
  await expect(detail).toBeVisible();
  await expect(detail.getByRole("link", { name: "Register on Eventbrite" })).toHaveAttribute(
    "href",
    "https://www.eventbrite.com/e/parkway-jazz-night",
  );
  await detail.getByRole("button", { name: "Close details" }).click();
  await expect(detail).toBeHidden();

  const manifestHref = await page.locator('link[rel="manifest"]').getAttribute("href");
  expect(manifestHref).not.toBeNull();
  const manifest = await page.evaluate(async (href) => {
    const response = await fetch(href);
    if (!response.ok) {
      throw new Error(`Manifest request failed: ${response.status}`);
    }
    return (await response.json()) as unknown;
  }, manifestHref as string);
  expect(manifest).toMatchObject({
    name: "Event Discovery Philadelphia",
    start_url: "/",
    display: "standalone",
  });

  const icons = (
    manifest as { icons: Array<{ src: string; sizes: string; purpose?: string }> }
  ).icons;
  expect(icons.some((icon) => icon.sizes === "192x192")).toBe(true);
  expect(icons.some((icon) => icon.sizes === "512x512")).toBe(true);
  expect(icons.some((icon) => icon.purpose === "maskable")).toBe(true);

  for (const expectedSize of [192, 512]) {
    const icon = icons.find(
      ({ sizes, purpose }) => sizes === `${expectedSize}x${expectedSize}` && purpose !== "maskable",
    );
    expect(icon).toBeDefined();
    const dimensions = await page.evaluate(async (src) => {
      const image = new Image();
      image.src = src;
      await image.decode();
      return { width: image.naturalWidth, height: image.naturalHeight };
    }, icon?.src as string);
    expect(dimensions).toEqual({ width: expectedSize, height: expectedSize });
  }

  await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.ready;
    if (registration.active === null) {
      throw new Error("Service worker did not activate");
    }
  });

  await page.reload();
  await expect(page.getByRole("status")).toHaveText("20 events");
  await expect
    .poll(() => page.evaluate(() => navigator.serviceWorker.controller !== null))
    .toBe(true);

  const cdp = await context.newCDPSession(page);
  const installability = await cdp.send("Page.getInstallabilityErrors");
  expect(installability.installabilityErrors).toEqual([]);

  await context.setOffline(true);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Find something worth going to." })).toBeVisible();
  await context.setOffline(false);
});
