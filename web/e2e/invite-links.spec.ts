import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";

test("share cancellation, copying, preview, Google signup and explicit acceptance", async ({ browser }, testInfo) => {
  const api = process.env.AUTH_E2E_API_URL;
  test.skip(!api, "Run from api/: python tests/run_auth_browser.py");
  const map = await readFile(new URL("./fixtures/google-maps.js", import.meta.url), "utf8");
  const ownerContext = await browser.newContext();
  const guestContext = await browser.newContext();
  async function prepare(page: Page, identity: string) {
    await page.addInitScript(() => {
      Object.defineProperty(window, "fixtureMapZoom", { value: 20 });
      Object.defineProperty(navigator, "share", { configurable: true, value: () => Promise.reject(new DOMException("Cancelled", "AbortError")) });
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
        writeText: (value: string) => { sessionStorage.setItem("fixture-copy", value); return Promise.resolve(); },
      } });
    });
    await page.route("https://maps.googleapis.com/maps/api/js?*", (route) => route.fulfill({ contentType: "application/javascript", body: map }));
    await page.route("**/api/categories", (route) => route.fulfill({ json: [] }));
    await page.route("**/similar", (route) => route.fulfill({ json: [] }));
    await page.goto(`${api}/fixture/identity/${identity}`);
  }
  try {
    const owner = await ownerContext.newPage();
    const guest = await guestContext.newPage();
    await prepare(owner, "sharer");
    await prepare(guest, "guest");
    await owner.getByRole("link", { name: "Sign in with Google", exact: true }).click();
    await expect(owner.getByText("Signed in as Sharer")).toBeVisible();
    await owner.goto("/?event=6d100000-0000-0000-0000-000000000001");
    await owner.getByRole("button", { name: "Share invite", exact: true }).click();
    await owner.getByLabel("Message for shared invite (optional)").fill("Come to Share night!");
    await owner.getByRole("button", { name: "Create link", exact: true }).click();
    await owner.getByRole("button", { name: "Share link", exact: true }).click();
    await expect(owner.getByText("Sharing cancelled. Your link is still available.")).toBeVisible();
    const created = await owner.request.get(`${api}/api/invite-links`);
    expect(await created.json()).toEqual([expect.objectContaining({ first_opened_at: null, acceptance_count: 0 })]);
    await owner.getByRole("button", { name: "Copy link", exact: true }).click();
    await expect(owner.getByText("Link copied. Paste it into your message.")).toBeVisible();
    const url = await owner.evaluate(() => sessionStorage.getItem("fixture-copy"));
    expect(url).toContain("#invite=");
    await guest.goto(url!);
    const invitation = guest.getByRole("dialog", { name: "Event invitation", exact: true });
    await expect(invitation.getByText("Share night", { exact: true })).toBeVisible();
    await expect(invitation.getByText("Come to Share night!")).toBeVisible();
    expect(guest.url()).not.toContain("#invite=");
    const previewed = await owner.request.get(`${api}/api/invite-links`);
    expect(await previewed.json()).toEqual([expect.objectContaining({ first_opened_at: expect.any(String), acceptance_count: 0 })]);
    await invitation.getByRole("link", { name: "Sign in with Google to accept" }).click();
    await expect(guest.getByText("Signed in as Guest")).toBeVisible();
    await expect(invitation.getByText("Share night", { exact: true })).toBeVisible();
    const afterSignup = await owner.request.get(`${api}/api/invite-links`);
    expect(await afterSignup.json()).toEqual([expect.objectContaining({ acceptance_count: 0 })]);
    await invitation.getByRole("button", { name: "Accept invitation", exact: true }).click();
    await expect(invitation.getByText("Accepted — you’re going")).toBeVisible();
    await guest.screenshot({ path: testInfo.outputPath("share-accepted-desktop.png") });
    await guest.setViewportSize({ width: 390, height: 844 });
    await guest.screenshot({ path: testInfo.outputPath("share-accepted-mobile.png") });
    expect(await guest.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await owner.getByRole("button", { name: "Close details" }).click();
    await owner.getByRole("button", { name: "Invitations", exact: true }).click();
    await owner.getByRole("button", { name: "Share links", exact: true }).click();
    const tracking = owner.getByRole("region", { name: "Share link tracking" });
    await expect(tracking.getByText("Accepted: 1", { exact: true })).toBeVisible();
    await expect(tracking.getByText(/Guest — accepted/)).toBeVisible();
    await tracking.getByRole("button", { name: "Revoke link" }).click();
    await expect(tracking.getByText("Revoked", { exact: true })).toBeVisible();
    await guest.goto(url!);
    await expect(invitation.getByRole("alert")).toHaveText("This invitation link is unavailable, expired, or revoked.");
    await expect(invitation.getByRole("button", { name: "Accept invitation" })).toHaveCount(0);
    const cached = await guest.evaluate(async () => {
      const urls: string[] = [];
      for (const name of await caches.keys()) urls.push(...(await (await caches.open(name)).keys()).map((request) => request.url));
      return urls;
    });
    expect(cached.some((entry) => entry.includes("/api/invite-links"))).toBe(false);
  } finally {
    await ownerContext.close();
    await guestContext.close();
  }
});
