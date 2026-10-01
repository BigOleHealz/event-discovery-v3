import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";

test("Google and vCard contacts invite a registered friend without SMS", async ({ page, context, browser }, testInfo) => {
  const api = process.env.AUTH_E2E_API_URL;
  test.skip(!api, "Run from api/: python tests/run_auth_browser.py");
  const map = await readFile(new URL("./fixtures/google-maps.js", import.meta.url), "utf8");
  await context.route("https://maps.googleapis.com/maps/api/js?*", (route) => route.fulfill({ contentType: "application/javascript", body: map }));
  await context.route("**/api/categories", (route) => route.fulfill({ json: [] }));
  await context.route("**/similar", (route) => route.fulfill({ json: [] }));
  await page.addInitScript(() => { Object.defineProperty(window, "fixtureMapZoom", { value: 20 }); });
  const friendContext = await browser.newContext();
  try {
    const friend = await friendContext.newPage();
    await friend.route("https://maps.googleapis.com/maps/api/js?*", (route) => route.fulfill({ contentType: "application/javascript", body: map }));
    await friend.route("**/api/categories", (route) => route.fulfill({ json: [] }));
    await friend.route("**/similar", (route) => route.fulfill({ json: [] }));
    await friend.goto(`${api}/fixture/identity/friend`);
    await friend.getByRole("link", { name: "Sign in with Google", exact: true }).click();
    await expect(friend.getByText("Signed in as Friend")).toBeVisible();
    await page.goto(`${api}/fixture/identity/contacts`);
    await page.getByRole("link", { name: "Sign in with Google", exact: true }).click();
    await expect(page.getByText("Signed in as Contacts")).toBeVisible();
    await page.getByRole("button", { name: "Contacts", exact: true }).click();
    await page.getByRole("link", { name: "Import Google Contacts" }).click();
    const panel = page.getByRole("dialog", { name: "Contacts", exact: true });
    await expect(panel.getByRole("listitem").filter({ hasText: "Unmatched Friend" })).toBeVisible();
    await panel.getByText("Import iPhone/iCloud contacts", { exact: true }).click();
    await expect(panel.getByText("On your iPhone, open Contacts and tap Lists.")).toBeVisible();
    await expect(panel.getByRole("link", { name: "Apple’s contact export instructions" }))
      .toHaveAttribute("href", "https://support.apple.com/guide/iphone/export-contacts-iph075ddebf2/ios");
    await panel.getByText("Import iPhone/iCloud contacts", { exact: true }).click();
    await panel.getByLabel("Import vCard (.vcf)").setInputFiles({
      name: "friends.vcf", mimeType: "text/vcard",
      buffer: Buffer.from("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Unmatched Friend\r\nTEL;TYPE=CELL:(415) 555-2671\r\nEND:VCARD\r\n"),
    });
    await expect(panel.getByText("Imported 1 contact addresses.")).toBeVisible();
    await expect(panel.getByRole("listitem").filter({ hasText: "Unmatched Friend" })).toHaveCount(1);
    await page.screenshot({ path: testInfo.outputPath("contacts-desktop.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: testInfo.outputPath("contacts-mobile.png") });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await panel.getByRole("button", { name: "Close contacts" }).click();
    await page.goto("/?event=6d000000-0000-0000-0000-000000000001");
    await expect(page.getByRole("dialog", { name: "Contact night", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Choose contacts (0 selected)" }).click();
    await expect(page.getByRole("checkbox", { name: /Unmatched Friend/ })).toBeDisabled();
    await page.getByRole("checkbox", { name: /Registered Friend/ }).check();
    await page.getByLabel("Message (optional)").fill("Come along!");
    await page.getByRole("button", { name: "Send invites" }).click();
    await expect(page.getByText("1 invitation saved. Check Sent for responses.")).toBeVisible();
    await page.getByRole("button", { name: "Close details" }).click();
    await page.getByRole("button", { name: "Invitations", exact: true }).click();
    const inbox = page.getByRole("dialog", { name: "Invitations" });
    await inbox.getByRole("button", { name: "Sent", exact: true }).click();
    await expect(inbox.getByText("Pending", { exact: true })).toBeVisible();
    await expect(inbox.getByRole("button", { name: "Retry SMS" })).toHaveCount(0);
    await friend.getByRole("button", { name: "Invitations", exact: true }).click();
    const received = friend.getByRole("dialog", { name: "Invitations" });
    await expect(received.getByText("Contact night", { exact: true })).toBeVisible();
    await received.getByRole("button", { name: "Accept", exact: true }).click();
    await expect(received.getByText("Accepted — you’re going")).toBeVisible();
    await inbox.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(inbox.getByText("Accepted", { exact: true })).toBeVisible();
    const cached = await page.evaluate(async () => {
      const urls: string[] = [];
      for (const name of await caches.keys()) urls.push(...(await (await caches.open(name)).keys()).map((request) => request.url));
      return urls;
    });
    expect(cached.some((url) => url.includes("/api/contacts") || url.includes("/api/invites"))).toBe(false);
  } finally {
    await friendContext.close();
  }
});
