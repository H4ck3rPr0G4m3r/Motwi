import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

// `npm run dev` proxies the API to a running Motwi (default port 47815).
const core = `http://127.0.0.1:${process.env.MOTWI_PORT ?? "47815"}`;

export default defineConfig({
  plugins: [svelte()],
  build: { outDir: "dist", emptyOutDir: true, assetsInlineLimit: 0 },
  server: {
    proxy: {
      "/api": {
        target: core,
        changeOrigin: true,
        ws: true,
        // the core only accepts WebSockets from its own origin
        headers: { Origin: core },
      },
    },
  },
});
