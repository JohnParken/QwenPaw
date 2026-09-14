import { defineConfig, loadEnv } from "vite";
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "QWENPAW_");
  return {
    server: {
      port: 5179,
      strictPort: true,
      proxy: {
        "/api": {
          target: env.QWENPAW_API_TARGET || "http://127.0.0.1:8088",
          changeOrigin: false,
        },
      },
    },
    preview: { port: 5180, strictPort: true },
    build: { target: "es2022", sourcemap: true },
  };
});
