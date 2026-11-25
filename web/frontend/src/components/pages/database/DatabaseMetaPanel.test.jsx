import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

import { DatabaseMetaPanel } from './DatabaseMetaPanel';

function renderWithMeta(metadata) {
  return render(
    <DatabaseMetaPanel
      selectedDatabase={{
        id: 'db1',
        display_name: 'Test DB',
        metadata,
      }}
    />,
  );
}

describe('DatabaseMetaPanel link and contact sanitization', () => {
  afterEach(() => {
    cleanup();
  });

  it('renders valid http/https websites and bare emails as links', () => {
    renderWithMeta({
      website: 'https://example.org/db',
      contact: 'team+support@sub.example.org',
    });

    const websiteLink = screen.getByRole('link', { name: 'https://example.org/db' });
    expect(websiteLink).toHaveAttribute('href', 'https://example.org/db');
    expect(websiteLink).toHaveAttribute('target', '_blank');
    expect(websiteLink).toHaveAttribute('rel', 'noreferrer');

    const contactLink = screen.getByRole('link', { name: 'team+support@sub.example.org' });
    expect(contactLink).toHaveAttribute('href', 'mailto:team+support@sub.example.org');
  });

  it('renders unsafe website URLs as plain text without an anchor', () => {
    const unsafeWebsites = [
      'javascript:alert(1)',
      'data:text/html,<script>alert(1)</script>',
      'vbscript:msgbox(1)',
      'file:///etc/passwd',
      '//evil.example/path',
      'http:example.com',
      'https://user:pass@evil.com',
      'https://www.who.int@evil.com/path',
      'https://user@example.org',
      'not-a-url',
    ];

    for (const website of unsafeWebsites) {
      cleanup();
      renderWithMeta({ website });
      expect(screen.getByText(website)).toBeInTheDocument();
      expect(screen.queryByRole('link')).not.toBeInTheDocument();
    }
  });

  it('renders unsafe or header-injected contact strings as plain text without a mailto anchor', () => {
    const unsafeContacts = [
      'user@example.org?subject=x&bcc=evil@attacker.com',
      'user@example.org#fragment',
      'user@example.org\nbcc:evil@attacker.com',
      'Alice <alice@example.org>',
      'a@b@example.org',
      'mailto:user@example.org',
      'Contact team @ lab',
    ];

    for (const contact of unsafeContacts) {
      cleanup();
      renderWithMeta({ contact });
      expect(screen.getByText((text) => text.includes(contact.split('\n')[0]))).toBeInTheDocument();
      expect(screen.queryByRole('link')).not.toBeInTheDocument();
    }
  });
});
