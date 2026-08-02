#!/usr/bin/env node
/**
 * Refuse to build the dashboard with an absolute API URL baked in.
 *
 * NEXT_PUBLIC_* values are inlined at build time, and apps/web/.env.local is
 * read automatically. A local-dev file pointing at http://127.0.0.1:8011 was
 * therefore compiled into the exported build and committed, so the deployed
 * dashboard on the VPS tried to fetch the API from the phone's own loopback
 * and failed with "Failed to fetch".
 *
 * The failure is silent: the build succeeds, the page renders, and only the
 * data is missing. Checking here is cheaper than noticing it in production.
 */
const fs = require("node:fs");
const path = require("node:path");

const webDir = path.join(__dirname, "..", "apps", "web");
const problems = [];

const fromEnv = process.env.NEXT_PUBLIC_TRADER_API_URL;
if (fromEnv) {
  problems.push(`NEXT_PUBLIC_TRADER_API_URL is set in the environment: ${fromEnv}`);
}

for (const name of [".env.local", ".env.production", ".env"]) {
  const file = path.join(webDir, name);
  if (!fs.existsSync(file)) continue;
  const hit = fs
    .readFileSync(file, "utf8")
    .split(/\r?\n/)
    .find((l) => /^\s*NEXT_PUBLIC_TRADER_API_URL\s*=\s*\S/.test(l));
  if (hit) problems.push(`apps/web/${name} sets it: ${hit.trim()}`);
}

if (problems.length) {
  console.error("\n  Refusing to build: the API URL would be hardcoded.\n");
  for (const p of problems) console.error(`    - ${p}`);
  console.error(
    [
      "",
      "  The dashboard is served by FastAPI on the same origin as the API, so",
      "  it must fetch a RELATIVE path. An absolute URL points every viewer at",
      "  their own machine instead of the server.",
      "",
      "  For deployment: remove or empty the setting, then rebuild.",
      "  For `next dev` against a backend on another port, pass it inline:",
      "      NEXT_PUBLIC_TRADER_API_URL=http://127.0.0.1:8011 npm run dev:web",
      "",
    ].join("\n")
  );
  process.exit(1);
}

console.log("  [ok] API URL is same-origin");
