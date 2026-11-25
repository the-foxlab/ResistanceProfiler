import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  FRONTEND_CONFIG,
  MAINTAINER_EMAIL,
  isSeparateOriginApiBase,
  resolveApiBase,
} from './config';

describe('config', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
    vi.resetModules();
  });

  it('defaults apiBase to empty string for same-origin requests and marks separate-origin unsupported', () => {
    expect(FRONTEND_CONFIG.apiBase).toBe('');
    expect(FRONTEND_CONFIG.ui.separateOriginSessionsSupported).toBe(false);
    expect(MAINTAINER_EMAIL).toBe('jonas.fuchs@uniklinik-freiburg.de');
    expect(FRONTEND_CONFIG.profile).toEqual({
      threads: 1,
      vcf: { minAf: 0.01, minDepth: 10 },
      jobPollIntervalMs: 2000,
    });
    expect(FRONTEND_CONFIG.batch).toEqual({
      minPollIntervalMs: 2000,
      maxPollIntervalMs: 30000,
      pollRequestsPerMinuteBudget: 80,
      maxConcurrentPolls: 5,
      defaultRetryAfterSeconds: 5,
      defaultSubmitCooldownSeconds: 60,
      maxPollBackoffMs: 60000,
      maxConsecutiveErrors: 10,
    });
    expect(FRONTEND_CONFIG.batch.pollRequestsPerMinuteBudget).toBeLessThanOrEqual(120);
  });

  it('defaults apiBase to same-origin even when window.location.port is 5173', async () => {
    const originalLocation = window.location;
    vi.stubGlobal('location', { ...originalLocation, origin: 'http://127.0.0.1:5173', port: '5173' });
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.resetModules();

    const mod = await import('./config');
    expect(mod.FRONTEND_CONFIG.apiBase).toBe('');
    expect(mod.FRONTEND_CONFIG.ui.explorerUrl).toBe('http://127.0.0.1:5173');
    expect(mod.FRONTEND_CONFIG.ui.separateOriginSessionsSupported).toBe(false);
    expect(warnSpy).not.toHaveBeenCalled();

    vi.unstubAllGlobals();
  });

  it('resolveApiBase trims whitespace and strips trailing slashes', () => {
    expect(resolveApiBase(undefined)).toBe('');
    expect(resolveApiBase(null)).toBe('');
    expect(resolveApiBase('')).toBe('');
    expect(resolveApiBase('   ')).toBe('');
    expect(resolveApiBase('/')).toBe('');
    expect(resolveApiBase('///')).toBe('');
    expect(resolveApiBase('  /sub/  ')).toBe('/sub');
    expect(resolveApiBase('http://127.0.0.1:8000/')).toBe('http://127.0.0.1:8000');
  });

  it('isSeparateOriginApiBase detects cross-origin API bases', () => {
    expect(isSeparateOriginApiBase('', 'http://127.0.0.1:5173')).toBe(false);
    expect(isSeparateOriginApiBase('/sub', 'http://127.0.0.1:5173')).toBe(false);
    expect(isSeparateOriginApiBase('http://127.0.0.1:5173', 'http://127.0.0.1:5173')).toBe(false);
    expect(isSeparateOriginApiBase('http://127.0.0.1:8000', 'http://127.0.0.1:5173')).toBe(true);
    expect(isSeparateOriginApiBase('https://api.example.org', 'https://app.example.org')).toBe(true);
    expect(isSeparateOriginApiBase('http://127.0.0.1:8000', '')).toBe(false);
  });

  it('warns when an explicit separate-origin VITE_RESPRO_API_BASE is configured', async () => {
    vi.stubEnv('VITE_RESPRO_API_BASE', 'http://127.0.0.1:8000/');
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.resetModules();

    const mod = await import('./config');
    expect(mod.FRONTEND_CONFIG.apiBase).toBe('http://127.0.0.1:8000');
    expect(warnSpy).toHaveBeenCalledTimes(1);
    expect(warnSpy.mock.calls[0][0]).toContain('Separate-origin browser sessions are unsupported');
  });
});
