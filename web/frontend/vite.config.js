import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const apiProxyTarget =
    env.RESPRO_API_PROXY_TARGET || env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000';

  return {
    plugins: [react({ fastRefresh: mode !== 'test' })],
    base: '/',
    build: {
      // Vite 8 URL-encodes inlined SVGs as data:image/svg+xml,... but does not
      // escape '#' characters (e.g. fill="#000000"). In a CSS url() context
      // (mask-image: var(--icon-src)) the '#' is parsed as a fragment identifier,
      // truncating the data URI and breaking icon rendering. Disabling inlining
      // forces all assets to be emitted as separate files with hashed URLs.
      assetsInlineLimit: 0,
      // Routes are lazy-loaded via React.lazy so plotly.js-dist-min is only fetched
      // on /databases and /analysis/reports. Because plotly.min.js is an irreducible
      // 4,293 kB pre-minified single file that Rolldown cannot subdivide, raise the
      // reporter limit just above that chunk while keeping the entry chunk (< 100 kB)
      // and all other chunks well under 500 kB.
      chunkSizeWarningLimit: 4500,
      rolldownOptions: {
        output: {
          codeSplitting: {
            groups: [
              {
                name: 'plotly',
                test: /node_modules[\\/]plotly\.js-dist-min[\\/]/,
                priority: 30,
              },
              {
                name: 'venn',
                test: /node_modules[\\/](?:venn\.js|d3-|fmin)[\\/]/,
                priority: 20,
              },
              {
                name: 'react-vendor',
                test: /node_modules[\\/](?:react|react-dom|react-router|scheduler)[\\/]/,
                priority: 10,
              },
            ],
          },
        },
      },
    },
    server: {
      host: '127.0.0.1',
      port: 5173,
      proxy: {
        '/api': {
          target: apiProxyTarget,
          changeOrigin: true,
        },
        '/legal': {
          target: apiProxyTarget,
          changeOrigin: true,
        },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.js'],
    },
  };
});
