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
