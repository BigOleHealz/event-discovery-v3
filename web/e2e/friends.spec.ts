import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";

test("accepted friends see share-invite acceptance on pins without changing category colour", async ({ browser }, testInfo) => {
  const api = process.env.AUTH_E2E_API_URL;
  test.skip(!api, "Run from api/: python tests/run_auth_browser.py");
  const map = await readFile(new URL("./fixtures/google-maps.js", import.meta.url), "utf8");
  const hostContext = await browser.newContext();
  const palContext = await browser.newContext();
  async function signIn(page: Page, identity: string) {
    await page.addInitScript(() => { Object.defineProperty(window, "fixtureMapZoom", { value: 20 }); });
    await page.route("https://maps.googleapis.com/maps/api/js?*", (route) => route.fulfill({ contentType: "application/javascript", body: map }));
    await page.route("**/api/categories", (route) => route.fulfill({ json: [] }));
    await page.route("**/similar", (route) => route.fulfill({ json: [] }));
    await page.route("https://images.example.test/**", (route) => route.abort());
    await page.goto(`${api}/fixture/identity/${identity}`);
    await page.getByRole("link", { name: "Sign in with Google", exact: true }).click();
    await expect(page.getByText(`Signed in as ${identity === "host" ? "Host" : "Pal"}`)).toBeVisible();
  }
  try {
    const host = await hostContext.newPage();
    const pal = await palContext.newPage();
    await signIn(host, "host"); await signIn(pal, "pal");
    await host.getByRole("button", { name: "Friends", exact: true }).click();
    await host.getByLabel("Friend’s account email").fill("pal@example.com");
    await host.getByRole("button", { name: "Send friend request" }).click();
    await expect(host.getByText("Pal", { exact: true })).toBeVisible();
    await host.getByRole("button", { name: "Close friends" }).click();
    await pal.getByRole("button", { name: "Friends", exact: true }).click();
    await pal.getByRole("button", { name: "Accept friend request" }).click();
    await expect(pal.getByRole("button", { name: "Remove friend" })).toBeVisible();
    await pal.getByRole("button", { name: "Close friends" }).click();
    const pin = host.locator('[data-event-marker="6e000000-0000-0000-0000-000000000001"]');
    await expect(pin).toBeVisible();
    const initialColour = await pin.locator("gmp-pin").evaluate((el) => (el as HTMLElement).style.background);
    await host.getByLabel("Friends going", { exact: true }).check();
    await expect(pin.locator(".friends-pin-badge")).toHaveCount(0);
    await pin.click();
    await host.getByRole("button", { name: "Share invite", exact: true }).click();
    await host.getByRole("button", { name: "Create link", exact: true }).click();
    const url = await host.getByLabel("Invitation link", { exact: true }).inputValue();
    await host.getByRole("button", { name: "Close details" }).click();
    await pal.goto(url);
    await pal.getByRole("button", { name: "Accept invitation", exact: true }).click();
    await expect(pal.getByText("Accepted — you’re going")).toBeVisible();
    await host.getByRole("button", { name: "Refresh friends going" }).click();
    await expect(pin.locator(".friends-pin-badge")).toBeVisible();
    await expect(pin).toHaveAttribute("title", "Friend night — 1 friend going");
    expect(await pin.locator("gmp-pin").evaluate((el) => (el as HTMLElement).style.background)).toBe(initialColour);
    await pin.click();
    await expect(host.getByRole("region", { name: "Friends going", exact: true }).getByText("Pal")).toBeVisible();
    await host.locator(".event-detail").evaluate(async (el) => {
      await Promise.all(el.getAnimations().map((animation) => animation.finished));
    });
    await host.screenshot({ path: testInfo.outputPath("friends-going-desktop.png") });
    await host.setViewportSize({ width: 390, height: 844 });
    await host.locator(".event-detail").evaluate(async (el) => {
      await Promise.all(el.getAnimations().map((animation) => animation.finished));
    });
    await host.screenshot({ path: testInfo.outputPath("friends-going-mobile.png") });
    expect(await host.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await host.getByRole("button", { name: "Close details" }).click();
    await host.getByRole("button", { name: "Friends", exact: true }).click();
    await host.getByRole("button", { name: "Remove friend" }).click();
    await host.getByRole("button", { name: "Close friends" }).click();
    await expect(pin.locator(".friends-pin-badge")).toHaveCount(0);
    await host.getByRole("button", { name: "Sign out" }).click();
    await expect(host.getByLabel("Friends going", { exact: true })).toHaveCount(0);
    const cached = await host.evaluate(async () => {
      const urls: string[] = [];
      for (const name of await caches.keys()) urls.push(...(await (await caches.open(name)).keys()).map((r) => r.url));
      return urls;
    });
    expect(cached.some((url) => url.includes("/friends"))).toBe(false);
  } finally { await hostContext.close(); await palContext.close(); }
});
