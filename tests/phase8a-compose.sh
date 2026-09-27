#!/usr/bin/env bash
set -euo pipefail

# Use a separate project so this check never stops the developer's existing stack.
project="event-discovery-phase8a-$$"
image="event-discovery-stagehand-test:phase8a-$$"
compose=(docker compose --project-name "$project" --env-file .env.example)
cleanup() {
  "${compose[@]}" down --volumes --remove-orphans
  docker image rm "$image" >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${compose[@]}" config --quiet
docker build --target test --tag "$image" ./stagehand
# The real browser sees only a synthetic loopback site, even if keys exist on the host.
docker run --rm --init --network none --shm-size=512m "$image" \
  sh -c 'npm run lint && npm run typecheck && npm test && npm run test:browser'
"${compose[@]}" up --detach --build --wait stagehand
"${compose[@]}" exec --no-TTY stagehand node --input-type=module -e '
  const base = `http://127.0.0.1:${process.env.STAGEHAND_PORT}`;
  const health = await fetch(`${base}/health`);
  if (!health.ok || (await health.json()).status !== "ok") process.exit(1);
  const unauthorized = await fetch(`${base}/v1/fetch`, {
    method: "POST", headers: {"content-type": "application/json"},
    body: JSON.stringify({url: `${base}/health`}),
  });
  if (unauthorized.status !== 401) process.exit(1);
  const rendered = await fetch(`${base}/v1/fetch`, {
    method: "POST",
    headers: {"content-type": "application/json", authorization: `Bearer ${process.env.STAGEHAND_API_TOKEN}`},
    body: JSON.stringify({url: `${base}/health`}),
  });
  const result = await rendered.json();
  if (!rendered.ok || result.fetch_method !== "stagehand" || !result.html.includes("ok")) process.exit(1);
  console.log("Production image: health, auth, and browser fetch passed");
'
