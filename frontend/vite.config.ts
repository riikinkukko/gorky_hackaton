import { defineConfig, loadEnv, type PluginOption } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";
import { viteSingleFile } from "vite-plugin-singlefile";

// Режимы:
//   vite               — работа с реальным бэкендом (прокси на VITE_BACKEND_URL)
//   vite --mode mock   — мок-API по контракту, бэкенд не нужен
//   vite build --mode preview — один HTML-файл с моком (для показа без сервера)
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const backend = env.VITE_BACKEND_URL || "http://localhost:8000";
  const single = mode === "preview";
  // Режимы mock и preview включают мок без .env-файлов (корневой .gitignore исключает .env.*)
  const mockMode = mode === "mock" || mode === "preview";
  const define = mockMode
    ? {
        "import.meta.env.VITE_USE_MOCK": JSON.stringify(env.VITE_USE_MOCK || "1"),
        "import.meta.env.VITE_FREEZE_NOW": JSON.stringify(env.VITE_FREEZE_NOW || "2026-10-21T14:00:00+03:00"),
      }
    : {};

  const plugins: PluginOption[] = [react(), tailwindcss()];
  if (single) {
    plugins.push(viteSingleFile());
  } else {
    plugins.push(
      VitePWA({
        registerType: "autoUpdate",
        includeAssets: ["icon.svg", "apple-touch-icon.png"],
        manifest: {
          name: "Помню — голосовой ассистент",
          short_name: "Помню",
          description: "Задачи, напоминания и встречи голосом",
          lang: "ru",
          theme_color: "#E2553A",
          background_color: "#F4F1EA",
          display: "standalone",
          start_url: "/",
          icons: [
            { src: "icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
            { src: "icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
            { src: "icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
            { src: "icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
          ],
        },
        workbox: {
          // API и голос никогда не кэшируем — только статику
          navigateFallbackDenylist: [/^\/api/, /^\/ws/, /^\/calendar/, /^\/health/],
        },
      }),
    );
  }

  return {
    define,
    plugins,
    server: {
      port: 5173,
      proxy: {
        "/api": backend,
        "/health": backend,
        "/calendar": backend,
        "/ws": { target: backend.replace(/^http/, "ws"), ws: true },
      },
    },
    build: single ? { assetsInlineLimit: 100_000_000, cssCodeSplit: false } : {},
  };
});
