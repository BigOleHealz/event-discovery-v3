import { readFile } from "node:fs/promises";
import { expect, test } from "@playwright/test";

test("Google and vCard contacts route an SMS invite to an event link", async ({ page, context }, testInfo) => {
  const api = process.env.AUTH_E2E_API_URL;
  test.skip(!api, "Run from api/: python tests/run_auth_browser.py");
  const map = await readFile(new URL("./fixtures/google-maps.js", import.meta.url), "utf8");
  await context.route("https://maps.googleapis.com/maps/api/js?*", (route) => route.fulfill({ contentType: "application/javascript", body: map }));
  await context.route("**/api/categories", (route) => route.fulfill({ json: [] }));
  await context.route("**/similar", (route) => route.fulfill({ json: [] }));
  await page.addInitScript(() => { Object.defineProperty(window, "fixtureMapZoom", { value: 20 }); });
  await page.goto(`${api}/fixture/identity/contacts`);
  await page.getByRole("link", { name: "Sign in with Google", exact: true }).click();
  await expect(page.getByText("Signed in as Contacts")).toBeVisible();
  await page.getByRole("button", { name: "Contacts", exact: true }).click();
  await page.getByRole("link", { name: "Import Google Contacts" }).click();
  const panel = page.getByRole("dialog", { name: "Contacts", exact: true });
  await expect(panel.getByRole("listitem").filter({ hasText: "SMS Friend" })).toBeVisible();
  await panel.getByLabel("Import vCard (.vcf)").setInputFiles({
    name: "friends.vcf", mimeType: "text/vcard",
    buffer: Buffer.from("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:SMS Friend\r\nTEL;TYPE=CELL:+1 (415) 555-2671\r\nEND:VCARD\r\n"),
  });
  await expect(panel.getByText("Imported 1 contact addresses.")).toBeVisible();
  await expect(panel.getByRole("listitem").filter({ hasText: "SMS Friend" })).toHaveCount(1);
  await page.screenshot({ path: testInfo.outputPath("contacts-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath("contacts-mobile.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await panel.getByRole("button", { name: "Close contacts" }).click();
  await page.locator('[data-event-marker="6b000000-0000-0000-0000-000000000001"]').click();
  await page.getByRole("button", { name: "Choose contacts (0 selected)" }).click();
  await page.getByRole("checkbox", { name: /SMS Friend/ }).check();
  await page.getByLabel("Message (optional)").fill("Come along!");
  await page.getByRole("button", { name: "Send invites" }).click();
  await expect(page.getByText("1 invitation saved. Check Sent for responses.")).toBeVisible();
  await page.getByRole("button", { name: "Close details" }).click();
  await page.getByRole("button", { name: "Invitations", exact: true }).click();
  const inbox = page.getByRole("dialog", { name: "Invitations" });
  await inbox.getByRole("button", { name: "Sent", exact: true }).click();
  await expect(inbox.getByText("SMS submitted for delivery")).toBeVisible();
  const captured = await page.request.get(`${api}/fixture/sms`);
  const messages = await captured.json() as Array<{ Body: string[]; To: string[] }>;
  expect(messages).toHaveLength(1);
  expect(messages[0].To).toEqual(["+14155552671"]);
  const eventLink = messages[0].Body[0].split("View event: ")[1];
  await page.goto(eventLink);
  await expect(page.getByRole("dialog", { name: "Invite night" })).toBeVisible();
  const cached = await page.evaluate(async () => {
    const urls: string[] = [];
    for (const name of await caches.keys()) urls.push(...(await (await caches.open(name)).keys()).map((request) => request.url));
    return urls;
  });
  expect(cached.some((url) => url.includes("/api/contacts") || url.includes("/api/invites"))).toBe(false);
});
