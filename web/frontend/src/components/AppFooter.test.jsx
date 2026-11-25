import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { AppFooter } from './AppFooter';

describe('AppFooter', () => {
  // Project links, the license, copyright, and optional deployment links and
  // version details share the footer in separate rows.

  it('renders project license and copyright when optional props are absent', () => {
    const { container } = render(<AppFooter />);
    expect(screen.queryByText('Legal notice')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /contact/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/respro CLI/)).not.toBeInTheDocument();
    expect(screen.queryByText(/respro WebApp/)).not.toBeInTheDocument();
    const copyright = screen.getByText('© 2026 Jonas Fuchs');
    const license = screen.getByRole('link', { name: 'License' });
    expect(copyright).toBeInTheDocument();
    expect(license).toHaveAttribute(
      'href',
      'https://github.com/the-foxlab/ResistanceProfiler/blob/master/LICENSE',
    );
    expect(license.parentElement).toBe(copyright.parentElement);
    expect(copyright.parentElement).toBe(container.querySelector('footer').lastElementChild);
  });

  it('renders a mailto contact link only when contactEmail is set', () => {
    render(<AppFooter contactEmail="support@example.org" />);
    const link = screen.getByRole('link', { name: /contact/i });
    expect(link).toHaveAttribute('href', 'mailto:support@example.org');
    expect(link).toHaveTextContent('Contact');
  });

  it('does not render a contact link when contactEmail is null', () => {
    render(<AppFooter contactEmail={null} />);
    expect(screen.queryByRole('link', { name: /contact/i })).not.toBeInTheDocument();
  });

  it('renders the legal notice link when legalLink is set', () => {
    render(<AppFooter legalLink="https://example.org/legal" />);
    const link = screen.getByRole('link', { name: /legal notice/i });
    expect(link).toHaveAttribute('href', 'https://example.org/legal');
  });

  it('renders same-origin /legal and subpath /legal links and rejects unsafe legalLink or contactEmail values', () => {
    const { rerender } = render(<AppFooter legalLink="/legal" />);
    expect(screen.getByRole('link', { name: /legal notice/i })).toHaveAttribute('href', '/legal');

    rerender(<AppFooter legalLink="/sub/legal" />);
    expect(screen.getByRole('link', { name: /legal notice/i })).toHaveAttribute('href', '/sub/legal');

    rerender(<AppFooter legalLink="javascript:alert(1)" contactEmail="user@example.org?bcc=evil@x.org" />);
    expect(screen.queryByRole('link', { name: /legal notice/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /contact/i })).not.toBeInTheDocument();

    rerender(<AppFooter legalLink="//evil.example/legal" contactEmail="mailto:user@example.org" />);
    expect(screen.queryByRole('link', { name: /legal notice/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /contact/i })).not.toBeInTheDocument();

    rerender(<AppFooter legalLink="/other" />);
    expect(screen.queryByRole('link', { name: /legal notice/i })).not.toBeInTheDocument();

    rerender(<AppFooter legalLink="https://user:pass@evil.com/legal" />);
    expect(screen.queryByRole('link', { name: /legal notice/i })).not.toBeInTheDocument();
  });

  it('places separators only within the link and version groups', () => {
    const { container } = render(
      <AppFooter
        legalLink="https://example.org/legal"
        contactEmail="support@example.org"
        cliVersion="1.2.3"
        webVersion="0.1.0"
      />,
    );
    const footer = container.querySelector('footer');
    expect(footer.lastElementChild).toContainElement(
      screen.getByRole('link', { name: 'License' }),
    );
    expect(footer.lastElementChild).toHaveTextContent('© 2026 Jonas Fuchs');
    // First row: GitHub · Fuchs Lab · Legal notice · Contact → 3 separators.
    // Version row: CLI · WebApp → 1; final License · copyright row → 1.
    const separators = container.querySelectorAll('.app-footer-sep');
    expect(separators).toHaveLength(5);
  });

  it('places a separator between legal and contact when versions are absent', () => {
    const { container } = render(
      <AppFooter legalLink="https://example.org/legal" contactEmail="support@example.org" />,
    );
    // First row: GitHub · Fuchs Lab · Legal notice · Contact → 3; final
    // License · copyright row → 1.
    const separators = container.querySelectorAll('.app-footer-sep');
    expect(separators).toHaveLength(4);
  });

  it('places no separator between the link and version groups', () => {
    const { container } = render(
      <AppFooter contactEmail="support@example.org" cliVersion="1.2.3" />,
    );
    // First row: GitHub · Fuchs Lab · Contact → 2; final License · copyright
    // row → 1 separator. The single version span has no separator.
    const separators = container.querySelectorAll('.app-footer-sep');
    expect(separators).toHaveLength(3);
  });

  it('handles sanitizeLegalHref edge inputs, webVersion-only row, and GitHub/Fuchs Lab links with aria-hidden icons', () => {
    const { container, rerender } = render(<AppFooter webVersion="0.2.1" />);
    expect(screen.getByText('respro WebApp v0.2.1')).toBeInTheDocument();
    expect(screen.queryByText(/respro CLI/)).not.toBeInTheDocument();
    // GitHub · Fuchs Lab (1) + License · Copyright (1) = 2 separators.
    expect(container.querySelectorAll('.app-footer-sep')).toHaveLength(2);

    const ghLink = screen.getByRole('link', { name: /resistanceprofiler on github/i });
    expect(ghLink).toHaveAttribute('href', 'https://github.com/the-foxlab/ResistanceProfiler');
    expect(ghLink.querySelector('[aria-hidden="true"]')).toBeInTheDocument();

    const labLink = screen.getByRole('link', { name: /jonas fuchs team website/i });
    expect(labLink).toHaveAttribute(
      'href',
      'https://www.uniklinik-freiburg.de/virologie-en/research/research-teams/jonas-fuchs-team.html',
    );
    expect(labLink.querySelector('[aria-hidden="true"]')).toBeInTheDocument();

    for (const bad of ['   ', 123, '/legal?lang=en', '/legal#imprint', '/legal\\x', '/legal/']) {
      rerender(<AppFooter legalLink={bad} />);
      expect(screen.queryByRole('link', { name: /legal notice/i })).not.toBeInTheDocument();
    }

    for (const valid of ['http://example.org/legal', '/prefix/legal']) {
      rerender(<AppFooter legalLink={valid} />);
      expect(screen.getByRole('link', { name: /legal notice/i })).toHaveAttribute('href', valid);
    }
  });
});
