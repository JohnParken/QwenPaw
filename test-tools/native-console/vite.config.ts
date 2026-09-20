import { defineConfig, loadEnv } from "vite";
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "QWENPAW_");
  const serviceTarget =
    env.QWENPAW_SERVICE_TARGET ||
    env.QWENPAW_TEST_API_URL ||
    "http://127.0.0.1:8092";
  const serviceToken =
    env.QWENPAW_SERVICE_TOKEN || env.QWENPAW_TEST_SERVICE_TOKEN || "";
  const serviceUser =
    env.QWENPAW_SERVICE_USER || env.QWENPAW_TEST_SERVICE_USER || "default";
  const modelDebugDefault = ["1", "true", "yes"].includes(
    (env.QWENPAW_MODEL_DEBUG_DEFAULT || env.QWENPAW_MODEL_DEBUG || "")
      .toLowerCase()
      .trim(),
  );
  const serviceProxy = {
    target: serviceTarget,
    changeOrigin: false,
    rewrite: (path: string) => {
      const suffix = path.slice("/api/service".length) || "";
      return suffix === "/health" || suffix === "/ready"
        ? suffix
        : `/v1${suffix}`;
    },
    headers: {
      Authorization: serviceToken ? `Bearer ${serviceToken}` : "",
      "X-QwenPaw-User": serviceUser,
    },
    configure: (proxyServer: {
      on: (
        event: string,
        listener: (request: {
          removeHeader: (name: string) => void;
          setHeader: (name: string, value: string) => void;
        }) => void,
      ) => void;
    }) => {
      proxyServer.on("proxyReq", (proxyReq) => {
        if (serviceToken)
          proxyReq.setHeader("authorization", `Bearer ${serviceToken}`);
        else proxyReq.removeHeader("authorization");
        proxyReq.setHeader("x-qwenpaw-user", serviceUser);
      });
    },
  };
  return {
    // The principal is a developer-selected identity, not a credential. The
    // bearer token remains in this server-only Vite config and is never
    // defined into the browser bundle.
    define: {
      __QWENPAW_SERVICE_USER__: JSON.stringify(serviceUser),
      __QWENPAW_CHAT_MODE__: JSON.stringify(
        env.QWENPAW_CHAT_MODE === "local" ? "local" : "service",
      ),
      __QWENPAW_MODEL_DEBUG_DEFAULT__: JSON.stringify(modelDebugDefault),
    },
    server: {
      port: 5179,
      strictPort: true,
      proxy: {
        "/api/service": serviceProxy,
        "/api": {
          target: env.QWENPAW_API_TARGET || "http://127.0.0.1:8088",
          changeOrigin: false,
        },
      },
    },
    preview: {
      port: 5180,
      strictPort: true,
      proxy: { "/api/service": serviceProxy },
    },
    build: { target: "es2022", sourcemap: true },
  };
});
