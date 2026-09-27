import { timingSafeEqual } from "node:crypto";
import Fastify, { LogController } from "fastify";
import { z } from "zod";
import { FetchFailure, type FetchPage } from "./browser.js";
import type { Config } from "./config.js";

const requestSchema = z.strictObject({
  url: z.url().max(8192).refine((value) => {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password;
  }),
  ready_selector: z.string().min(1).max(1024).optional(),
  steps: z.array(z.strictObject({
    selector: z.string().min(1).max(1024),
    wait_for_selector: z.string().min(1).max(1024),
    delay_ms: z.number().int().min(1).max(3_600_000),
  })).max(20).optional(),
});

export function createServer(config: Config, fetchPage: FetchPage) {
  const app = Fastify({
    bodyLimit: 16_384,
    requestTimeout: 10_000,
    logger: { redact: ["req.headers.authorization"] },
    // Request URLs, target URLs, page contents, and browser errors can contain secrets.
    logController: new LogController({ disableRequestLogging: true }),
  });
  let active = 0;
  const expected = Buffer.from(`Bearer ${config.apiToken}`);

  app.get("/health", () => ({ status: "ok" }));
  app.post("/v1/fetch", {
    onRequest: async (request, reply) => {
      const actual = Buffer.from(request.headers.authorization ?? "");
      if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
        return reply.code(401).send({ error: "unauthorized" });
      }
    },
  }, async (request, reply) => {
    const parsed = requestSchema.safeParse(request.body);
    if (!parsed.success) return reply.code(400).send({ error: "invalid_request" });
    if (active >= config.maxConcurrency) {
      return reply.code(503).header("Retry-After", "1").send({ error: "browser_busy" });
    }
    active += 1;
    try {
      return await fetchPage(parsed.data);
    } catch (error) {
      const code = error instanceof FetchFailure ? error.code : "browser_failure";
      app.log.warn({ code }, "Browser fetch failed");
      return reply.code(code === "fetch_timeout" ? 504 : 502).send({ error: code });
    } finally {
      active -= 1;
    }
  });
  app.setErrorHandler((error, _request, reply) => {
    const status = error instanceof Error && "statusCode" in error && typeof error.statusCode === "number"
      ? error.statusCode : 500;
    return reply.code(status).send({ error: status === 413 ? "request_too_large" : status < 500 ? "invalid_request" : "internal_error" });
  });
  return app;
}
