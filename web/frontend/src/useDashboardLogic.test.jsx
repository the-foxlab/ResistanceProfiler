import { describe, expect, it } from 'vitest';

import { _resolveContactEmail, _resolveLegalLink, webVersion } from './useDashboardLogic';

describe('dashboard legal and contact link helpers', () => {
  it('exports webVersion from package.json', () => {
    expect(typeof webVersion).toBe('string');
    expect(webVersion.length).toBeGreaterThan(0);
  });

  it('resolves legal links safely and rejects non-HTTP schemes', () => {
    expect(_resolveLegalLink(null)).toBeNull();
    expect(_resolveLegalLink({ enabled: false, kind: 'html' })).toBeNull();
    expect(_resolveLegalLink({ enabled: true, kind: 'html' })).toMatch(/\/legal$/);
    expect(_resolveLegalLink({ enabled: true, kind: 'url', url: '' })).toMatch(/\/legal$/);
    expect(_resolveLegalLink({ enabled: true, kind: 'url', url: 'https://example.org/imprint' })).toBe(
      'https://example.org/imprint',
    );
    expect(_resolveLegalLink({ enabled: true, kind: 'url', url: 'javascript:alert(1)' })).toBeNull();
    expect(_resolveLegalLink({ enabled: true, kind: 'url', url: 'data:text/html,hi' })).toBeNull();
  });

  it('resolves contact email safely and rejects malformed values', () => {
    expect(_resolveContactEmail(null)).toBeNull();
    expect(_resolveContactEmail({ enabled: false, email: 'a@b.org' })).toBeNull();
    expect(_resolveContactEmail({ enabled: true, email: 'support@example.org' })).toBe('support@example.org');
    expect(_resolveContactEmail({ enabled: true, email: 'bad email\n@example.org' })).toBeNull();
  });
});
