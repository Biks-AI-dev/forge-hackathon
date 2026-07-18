import { defineConfig } from "vite";

// Builds straight into ../static, which FastAPI already serves:
// GET / -> FileResponse(static/index.html), /static/* -> StaticFiles(static/).
// base must match the StaticFiles mount path so built asset URLs resolve.
export default defineConfig({
  base: "/static/",
  build: {
    outDir: "../static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/jobs": "http://127.0.0.1:8901",
      "/research": "http://127.0.0.1:8901",
      "/health": "http://127.0.0.1:8901",
    },
  },
});
