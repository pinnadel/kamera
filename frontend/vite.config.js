import { defineConfig } from 'vite'
import { resolve } from 'path'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      // selfDestroying: the app is a LOCAL desktop tool — its backend is always
      // on localhost, so offline precaching buys nothing and the service worker
      // only causes harm: its navigateFallback served a stale index.html for
      // download navigations (e.g. taste-model export), saving the app shell
      // HTML instead of the real file. This mode ships a SW whose sole job is to
      // unregister any previously-installed SW and delete its caches, then never
      // register again. Keeps the web-manifest (installability) intact.
      selfDestroying: true,
      registerType: 'autoUpdate',
      injectRegister: 'auto',
      includeAssets: [
        'favicon.svg',
        'favicon-dark.svg',
        'favicon-light.ico',
        'favicon-dark.ico',
        'apple-touch-icon.png',
      ],
      manifest: {
        name: 'KaMeRa',
        short_name: 'KaMeRa',
        description: 'AI-powered photo culling assistant',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        background_color: '#07080a',
        theme_color: '#07080a',
        icons: [
          { src: 'pwa-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'pwa-512-maskable.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // Shell-only: precache the built JS/CSS/HTML/icons. API calls and
        // /previews/* are NOT cached — always go to network so culling state
        // and thumbnails are never stale.
        globPatterns: ['**/*.{js,css,html,svg,ico,png,webmanifest}'],
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [
          /^\/api\//,
          /^\/previews\//,
          /^\/images($|\/)/,
          /^\/analyze/,
          /^\/health/,
          /^\/similarity-groups/,
          /^\/face-groups/,
          /^\/generate-explanation/,
          /^\/rank-burst/,
          /^\/stop-analysis/,
          /^\/sync-mobile/,
          /^\/pick-folder/,
          /^\/mobile\.html/,
          // Taste-model backup download — a GET that returns a JSON attachment.
          // Without this the SW's navigateFallback serves index.html instead of
          // the file. (Import is a POST, which the fallback ignores already.)
          /^\/export-taste-model/,
        ],
      },
      devOptions: {
        enabled: false,
      },
    }),
  ],
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        mobile: resolve(__dirname, 'mobile.html'),
      },
    },
  },
})
