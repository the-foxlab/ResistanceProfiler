const DEFAULT_DEV_SERVER_PORT = '5173';

const defaultApiBase =
  typeof window !== 'undefined' && window.location.port === DEFAULT_DEV_SERVER_PORT
    ? 'http://127.0.0.1:8000'
    : '';

export const FRONTEND_CONFIG = {
  apiBase: import.meta.env.VITE_RESPRO_API_BASE || defaultApiBase,
  apiToken: (import.meta.env.VITE_RESPRO_API_TOKEN || '').trim(),
  profile: {
    aligner: 'mappy',
    threads: 1,
    vcf: {
      minAf: 0.01,
      minDepth: 10,
    },
    jobPollIntervalMs: 2000,
  },
  defaults: {
    sampleName: 'sample',
  },
  ui: {
    explorerUrl: 'http://127.0.0.1:8000/app',
  },
};
