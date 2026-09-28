import { z } from "zod";

const positiveInteger = z.coerce.number().int().positive();

const environmentSchema = z.object({
  STAGEHAND_HOST: z.string().min(1),
  STAGEHAND_PORT: positiveInteger.max(65535),
  STAGEHAND_API_TOKEN: z.string().min(24),
  STAGEHAND_BROWSER_EXECUTABLE: z.string().min(1),
  STAGEHAND_BROWSER_SANDBOX: z.enum(["true", "false"]),
  STAGEHAND_FETCH_TIMEOUT_MS: positiveInteger.max(120_000),
  STAGEHAND_MAX_CONCURRENCY: positiveInteger.max(16),
  STAGEHAND_MAX_HTML_BYTES: positiveInteger.max(20_000_000),
});

export type Config = ReturnType<typeof loadConfig>;

export function loadConfig(env: NodeJS.ProcessEnv = process.env) {
  const result = environmentSchema.safeParse(env);
  if (!result.success) {
    // Report names only: never include environment values (especially the token).
    throw new Error(`Invalid configuration: ${result.error.issues.map((issue) => issue.path.join(".")).join(", ")}`);
  }
  const value = result.data;
  return {
    host: value.STAGEHAND_HOST,
    port: value.STAGEHAND_PORT,
    apiToken: value.STAGEHAND_API_TOKEN,
    executablePath: value.STAGEHAND_BROWSER_EXECUTABLE,
    browserSandbox: value.STAGEHAND_BROWSER_SANDBOX === "true",
    fetchTimeoutMs: value.STAGEHAND_FETCH_TIMEOUT_MS,
    maxConcurrency: value.STAGEHAND_MAX_CONCURRENCY,
    maxHtmlBytes: value.STAGEHAND_MAX_HTML_BYTES,
  };
}
