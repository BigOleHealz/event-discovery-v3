import { localBrowser, Stagehand, type StagehandBrowser } from "@browserbasehq/stagehand";
import type { Config } from "./config.js";

export interface FetchRequest {
  url: string;
  ready_selector?: string | undefined;
  steps?: { selector: string; wait_for_selector: string; delay_ms: number }[] | undefined;
}

export interface FetchResult {
  url: string;
  http_status: number | null;
  title: string;
  html: string;
  bytes: number;
  fetch_method: "stagehand";
}

export class FetchFailure extends Error {
  constructor(public readonly code: "fetch_timeout" | "page_too_large" | "browser_failure") {
    super(code);
  }
}

type BrowserSession = { context: Pick<StagehandBrowser["context"], "newPage">; close(): Promise<void> };
export type LaunchBrowser = () => Promise<BrowserSession>;
export type FetchPage = (request: FetchRequest) => Promise<FetchResult>;

export function browserLauncher(config: Config): LaunchBrowser {
  return async () => {
    const browser = await localBrowser.launch({
      executablePath: config.executablePath,
      chromiumSandbox: config.browserSandbox,
      headless: true,
      acceptDownloads: false,
      args: ["--disable-dev-shm-usage", "--disable-background-networking"],
    });
    try {
      const session = await Stagehand.create({ browser, logging: { level: "off" } });
      return {
        context: browser.context,
        close: async () => {
          // Closing the session can wait behind a stuck navigation. Start it, then
          // terminate Chromium; a disconnected-session error is expected on abort.
          const closingSession = session.close().catch(() => undefined);
          try { await browser.close(); } finally { await closingSession; }
        },
      };
    } catch (error) {
      await browser.close();
      throw error;
    }
  };
}

// A fresh browser/profile per call prevents cookies and local storage crossing sources.
// No model API is used here; derivation and persistent caching belong to Phase 8b.
export function createFetcher(config: Config, launch = browserLauncher(config)): FetchPage {
  return async (request) => {
    let expired = false;
    let browser: BrowserSession | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const deadline = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        expired = true;
        reject(new FetchFailure("fetch_timeout"));
      }, config.fetchTimeoutMs);
    });
    // If launch completes after the deadline, close it before allowing any navigation.
    const launching = launch().then(async (launched) => {
      if (expired) {
        await launched.close();
        throw new FetchFailure("fetch_timeout");
      }
      browser = launched;
      return launched;
    });
    try {
      const work = async (): Promise<FetchResult> => {
        const current = await launching;
        const page = await current.context.newPage();
        const response = await page.goto(request.url, {
          waitUntil: "load",
          timeout: config.fetchTimeoutMs,
        });
        if (request.ready_selector) {
          const ready = await page.waitForSelector(request.ready_selector, {
            timeout: config.fetchTimeoutMs,
          });
          if (!ready) throw new FetchFailure("fetch_timeout");
        }
        for (const step of request.steps ?? []) {
          await page.waitForTimeout(step.delay_ms);
          await page.locator(step.selector).click();
          if (!await page.waitForSelector(step.wait_for_selector, {
            timeout: config.fetchTimeoutMs,
          })) throw new FetchFailure("fetch_timeout");
        }
        const snapshot = await page.evaluate((maxBytes) => {
          const html = document.documentElement.outerHTML;
          const bytes = new TextEncoder().encode(html).byteLength;
          return { url: location.href, title: document.title, html: bytes <= maxBytes ? html : null, bytes };
        }, config.maxHtmlBytes);
        if (snapshot.html === null) throw new FetchFailure("page_too_large");
        return { ...snapshot, html: snapshot.html, http_status: response?.status() ?? null, fetch_method: "stagehand" };
      };
      return await Promise.race([work(), deadline]);
    } catch (error) {
      if (error instanceof FetchFailure) throw error;
      throw new FetchFailure("browser_failure");
    } finally {
      clearTimeout(timer);
      // Keep the request's capacity slot until startup and cleanup have finished.
      // Stagehand itself bounds local launch at 60s; no orphan launches accumulate.
      await launching.catch(() => undefined);
      if (browser) await browser.close();
    }
  };
}
