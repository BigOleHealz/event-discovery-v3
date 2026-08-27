import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { VitePWA } from "vite-plugin-pwa";

const apiProxy = process.env.VITE_PROXY_API_TARGET
  ? {
      "/api": {
        target: process.env.VITE_PROXY_API_TARGET,
        changeOrigin: true,
      },
    }
  : undefined;

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      registerType: "autoUpdate",
      injectRegister: "script-defer",
      includeAssets: ["icons/*.png", "icons/*.svg"],
      manifest: {
        name: "Event Discovery Philadelphia",
        short_name: "Event Discovery",
        description: "A map-first guide to in-person events around Philadelphia.",
        start_url: "/",
        scope: "/",
        display: "standalone",
        background_color: "#efe9dc",
        theme_color: "#18231b",
        orientation: "any",
        icons: [
          { src: "/icons/icon-48.png", sizes: "48x48", type: "image/png" },
          { src: "/icons/icon-72.png", sizes: "72x72", type: "image/png" },
          { src: "/icons/icon-96.png", sizes: "96x96", type: "image/png" },
          { src: "/icons/icon-128.png", sizes: "128x128", type: "image/png" },
          { src: "/icons/icon-144.png", sizes: "144x144", type: "image/png" },
          { src: "/icons/icon-152.png", sizes: "152x152", type: "image/png" },
          { src: "/icons/icon-167.png", sizes: "167x167", type: "image/png" },
          { src: "/icons/icon-180.png", sizes: "180x180", type: "image/png" },
          { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "/icons/icon-256.png", sizes: "256x256", type: "image/png" },
          { src: "/icons/icon-384.png", sizes: "384x384", type: "image/png" },
          { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
          {
            src: "/icons/maskable-192.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "maskable",
          },
          {
            src: "/icons/maskable-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      injectManifest: {
        globPatterns: ["**/*.{html,js,css,svg,png,webmanifest}"],
      },
      devOptions: {
        enabled: true,
        type: "module",
        navigateFallback: "index.html",
        navigateFallbackAllowlist: [/^\/$/],
      },
    }),
  ],
  server: {
    strictPort: true,
    proxy: apiProxy,
  },
  preview: {
    strictPort: true,
    proxy: apiProxy,
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
