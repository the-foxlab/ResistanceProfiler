import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => ({
  plugins: [react({ fastRefresh: mode !== 'test' })],
  base: '/',
  build: {
    // Vite 8 URL-encodes inlined SVGs as data:image/svg+xml,... but does not
    // escape '#' characters (e.g. fill="#000000"). In a CSS url() context
    // (mask-image: var(--icon-src)) the '#' is parsed as a fragment identifier,
    // truncating the data URI and breaking icon rendering. Disabling inlining
    // forces all assets to be emitted as separate files with hashed URLs.
    assetsInlineLimit: 0,
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
  },
}));
