const DEFAULT_DEV_SERVER_PORT = '5173';

const defaultApiBase =
  typeof window !== 'undefined' && window.location.port === DEFAULT_DEV_SERVER_PORT
    ? 'http://127.0.0.1:8000'
    : '';

// URL of the running web app, shown on the About page. In production the
// backend serves the frontend same-origin (apiBase ''), so the app's own
// origin is the URL to open; in dev the backend host is the deployed app.
const explorerUrl =
  typeof window !== 'undefined'
    ? (defaultApiBase || window.location.origin)
    : '';

// Maintainer address shown when the deployment does not configure
// RESPRO_WEB_CONTACT_EMAIL (see AboutPage).
export const MAINTAINER_EMAIL = 'jonas.fuchs@uniklinik-freiburg.de';

export const FRONTEND_CONFIG = {
  apiBase: import.meta.env.VITE_RESPRO_API_BASE || defaultApiBase,
  profile: {
    threads: 1,
    vcf: {
      minAf: 0.01,
      minDepth: 10,
    },
    jobPollIntervalMs: 2000,
  },
  defaults: {
    sampleName: '',
  },
  ui: {
    explorerUrl,
  },
};
