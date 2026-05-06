import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backendHttp = env.BACKEND_URL ?? "http://localhost:8003";
  const backendWs   = backendHttp.replace(/^https/, "wss").replace(/^http/, "ws");

  return {
    plugins: [react()],
    server: {
      port: 3000,
      proxy: {
        "/api": { target: backendHttp, changeOrigin: true },
        "/ws":  { target: backendWs,   ws: true, changeOrigin: true },
      },
    },
    build: { outDir: "dist" },
  };
});
