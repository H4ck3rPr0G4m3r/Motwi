// Builds the extension into dist/. Content scripts must be single classic scripts, so each
// entry is its own IIFE build rather than one multi-entry build with shared chunks.

import { cpSync, mkdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { build } from "vite";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = resolve(root, "dist");

const ENTRIES = {
  worker: "src/worker/index.ts",
  content: "src/content/main.ts",
  "page-world": "src/content/page-world.ts",
  pair: "src/pair/main.ts",
  popup: "src/popup/main.ts",
};

const FONTS = [
  ["inter", "inter-latin-400-normal.woff2"],
  ["inter", "inter-latin-600-normal.woff2"],
  ["inter", "inter-latin-700-normal.woff2"],
  ["jetbrains-mono", "jetbrains-mono-latin-600-normal.woff2"],
];

rmSync(dist, { recursive: true, force: true });
for (const [name, entry] of Object.entries(ENTRIES)) {
  await build({
    root,
    configFile: false,
    logLevel: "warn",
    plugins: [svelte({ configFile: false, emitCss: false })],
    define: { "process.env.NODE_ENV": '"production"' },
    build: {
      outDir: dist,
      emptyOutDir: false,
      minify: true,
      sourcemap: false,
      lib: {
        entry: resolve(root, entry),
        name: "motwi",
        formats: ["iife"],
        fileName: () => `${name}.js`,
      },
    },
  });
}

cpSync(resolve(root, "public"), dist, { recursive: true });
mkdirSync(resolve(dist, "fonts"), { recursive: true });
for (const [pkg, file] of FONTS) {
  cpSync(
    resolve(root, "node_modules/@fontsource", pkg, "files", file),
    resolve(dist, "fonts", file),
  );
}
console.log("built extension/dist");
