import { readFile } from "node:fs/promises";
import assert from "node:assert/strict";
const pkg = JSON.parse(
  await readFile(new URL("../package.json", import.meta.url), "utf8"),
);
const allowed = {
  typescript: "^5.7.3",
  "@types/node": "^24.7.1",
  vite: "5.1.8",
  tsup: "7.2.0",
  "rollup-plugin-visualizer": "^5.12.0",
  vitest: "0.32.0",
  jsdom: "19.0.0",
  "@playwright/test": "1.61.1",
  eslint: "^8.51.0",
  "@eslint/js": "^8.57.1",
  "typescript-eslint": "^8.7.0",
  "eslint-plugin-import": "^2.26.0",
  globals: "^13.24.0",
  prettier: "^3.1.1",
  "@commitlint/cli": "^18.4.3",
  "@commitlint/config-conventional": "^18.4.3",
  husky: "^8.0.3",
  "lint-staged": "^15.2.0",
  dotenv: "^16.3.1",
};
assert.equal(
  Object.keys(pkg.dependencies || {}).length,
  0,
  "Browser runtime dependencies must be empty",
);
for (const section of [
  "devDependencies",
  "dependencies",
  "optionalDependencies",
  "peerDependencies",
]) {
  for (const [name, range] of Object.entries(pkg[section] || {}))
    assert.equal(
      range,
      allowed[name],
      `Unapproved dependency/version: ${name}`,
    );
}
assert.ok(!pkg.overrides, "No dependency overrides");
console.log(
  "Direct dependency names/ranges match the allowlist; no runtime dependencies.",
);
