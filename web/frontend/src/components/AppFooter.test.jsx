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
});
