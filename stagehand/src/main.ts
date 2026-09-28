import { browserLauncher, createFetcher } from "./browser.js";
import { loadConfig } from "./config.js";
import { createServer } from "./server.js";

const config = loadConfig();
// Fail startup if Chromium or Stagehand's bundled runtime cannot launch.
const probe = await browserLauncher(config)();
await probe.close();
const app = createServer(config, createFetcher(config));
for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.once(signal, () => {
    // Fastify drains in-flight requests; each fetch closes its own browser.
    void app.close().catch(() => { process.exitCode = 1; });
  });
}
await app.listen({ host: config.host, port: config.port });
