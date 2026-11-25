/**
 * Frontend runtime configuration.
 *
 * Same-origin browser sessions are required: in Vite dev mode `/api` and
 * `/legal` are proxied to the backend, and in production FastAPI serves the
 * built SPA and `/api` on the same origin. Separate-origin browser sessions
 * (setting `VITE_RESPRO_API_BASE` to a different origin) are explicitly
 * unsupported because session cookies are same-origin and CORS disables
 * credentials.
 */

export function resolveApiBase(rawValue) {
  if (rawValue === null || rawValue === undefined) {
    return '';
  }
  const trimmed = String(rawValue).trim().replace(/\/+$/, '');
  return trimmed === '/' ? '' : trimmed;
}

export function isSeparateOriginApiBase(apiBase, locationOrigin) {
  if (!apiBase || !locationOrigin) {
    return false;
  }
  try {
    const baseOrigin = new URL(locationOrigin).origin;
    const resolvedOrigin = new URL(apiBase, baseOrigin).origin;
    return resolvedOrigin !== baseOrigin;
  } catch {
    return false;
  }
}

const currentOrigin = typeof window !== 'undefined' ? window.location.origin : '';
const apiBase = resolveApiBase(import.meta.env.VITE_RESPRO_API_BASE);

if (isSeparateOriginApiBase(apiBase, currentOrigin)) {
  console.warn(
    `[respro] VITE_RESPRO_API_BASE="${apiBase}" points to a separate origin (${currentOrigin}). `
    + 'Separate-origin browser sessions are unsupported; use same-origin hosting or the Vite /api dev proxy.',
  );
}

// URL of the running web app shown on About; always same-origin.
const explorerUrl = currentOrigin;

// Maintainer address shown when RESPRO_WEB_CONTACT_EMAIL is unset (AboutPage).
export const MAINTAINER_EMAIL = 'jonas.fuchs@uniklinik-freiburg.de';

export const FRONTEND_CONFIG = {
  apiBase,
  profile: {
    threads: 1,
    vcf: {
      minAf: 0.01,
      minDepth: 10,
    },
    jobPollIntervalMs: 2000,
  },
  batch: {
    minPollIntervalMs: 2000,
    maxPollIntervalMs: 30000,
    pollRequestsPerMinuteBudget: 80,
    maxConcurrentPolls: 5,
    defaultRetryAfterSeconds: 5,
    defaultSubmitCooldownSeconds: 60,
    maxPollBackoffMs: 60000,
    maxConsecutiveErrors: 10,
  },
  ui: {
    explorerUrl,
    // Separate-origin browser sessions are unsupported (see module header).
    separateOriginSessionsSupported: false,
  },
};
