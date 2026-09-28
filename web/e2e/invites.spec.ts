import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";

test("a friend receives an invite and the sender sees acceptance", async ({ browser }, testInfo) => {
  const api = process.env.AUTH_E2E_API_URL;
  test.skip(!api, "Run from api/: python tests/run_auth_browser.py");
  const map = await readFile(new URL("./fixtures/google-maps.js", import.meta.url), "utf8");
  const senderContext = await browser.newContext();
  const recipientContext = await browser.newContext();
  async function signIn(page: Page, name: string) {
    await page.addInitScript(() => { Object.defineProperty(window, "fixtureMapZoom", { value: 20 }); });
    await page.route("https://maps.googleapis.com/maps/api/js?*", (route) => route.fulfill({
      contentType: "application/javascript", body: map,
    }));
    await page.route("**/api/categories", (route) => route.fulfill({ json: [] }));
    await page.route("**/similar", (route) => route.fulfill({ json: [] }));
    await page.goto(`${api}/fixture/identity/${name}`);
    await page.getByRole("link", { name: "Sign in with Google", exact: true }).click();
    await expect(page.getByText(`Signed in as ${name === "sender" ? "Sender" : "Recipient"}`)).toBeVisible();
  }
  try {
    const sender = await senderContext.newPage();
    const recipient = await recipientContext.newPage();
    await signIn(recipient, "recipient");
    await signIn(sender, "sender");
    await sender.locator('[data-event-marker="6b000000-0000-0000-0000-000000000001"]').click();
    await sender.getByLabel("Friends’ account emails").fill("recipient@example.com");
    await sender.getByLabel("Message (optional)").fill("See you there!");
    await sender.getByRole("button", { name: "Send invites" }).click();
    await expect(sender.getByText("1 invitation saved. Check Sent for responses.")).toBeVisible();
    await sender.getByRole("button", { name: "Close details" }).click();
    await sender.getByRole("button", { name: "Invitations", exact: true }).click();
    const sent = sender.getByRole("dialog", { name: "Invitations" });
    await sent.getByRole("button", { name: "Sent", exact: true }).click();
    await expect(sent.getByText("Pending", { exact: true })).toBeVisible();
    await recipient.getByRole("button", { name: "Invitations", exact: true }).click();
    const inbox = recipient.getByRole("dialog", { name: "Invitations" });
    await expect(inbox.getByText("Invite night", { exact: true })).toBeVisible();
    await expect(inbox.getByText("From Sender", { exact: true })).toBeVisible();
    await expect(inbox.getByText("See you there!", { exact: true })).toBeVisible();
    await inbox.getByRole("button", { name: "Accept", exact: true }).click();
    await expect(inbox.getByText("Accepted — you’re going")).toBeVisible();
    await expect(inbox.getByRole("button", { name: "Accept", exact: true })).toBeDisabled();
    await sent.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(sent.getByText("Accepted", { exact: true })).toBeVisible();
    await recipient.screenshot({ path: testInfo.outputPath("invite-desktop.png") });
    await recipient.setViewportSize({ width: 390, height: 844 });
    await recipient.screenshot({ path: testInfo.outputPath("invite-mobile.png") });
    expect(await recipient.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await inbox.getByRole("button", { name: "Decline", exact: true }).click();
    await expect(inbox.getByText("Declined", { exact: true })).toBeVisible();
    await inbox.getByRole("button", { name: "Accept", exact: true }).click();
    await expect(inbox.getByText("Accepted — you’re going")).toBeVisible();
    const cached = await recipient.evaluate(async () => {
      const requests: string[] = [];
      for (const name of await caches.keys()) {
        requests.push(...(await (await caches.open(name)).keys()).map((request) => request.url));
      }
      return requests;
    });
    expect(cached.some((url) => url.includes("/api/invites"))).toBe(false);
    await inbox.getByRole("button", { name: "Close invitations" }).click();
    await recipient.getByRole("button", { name: "Sign out" }).click();
    await expect(recipient.getByRole("link", { name: "Sign in with Google", exact: true })).toBeVisible();
    await expect(recipient.getByRole("dialog", { name: "Invitations" })).toHaveCount(0);
  } finally {
    await senderContext.close();
    await recipientContext.close();
  }
});
