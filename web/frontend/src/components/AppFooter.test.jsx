import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { AppFooter } from './AppFooter';

describe('AppFooter', () => {
  // The footer renders the fixed project links (GitHub, Fuchs Lab) at the head
  // of the first row, followed by optional Legal notice and Contact links, plus
  // version spans on the second row. Each neighbour pair is separated by a `·`
  // only when both are present, so the separator count equals the number of
  // adjacent present items minus one. The two project links always contribute
  // one separator between them, plus one before the next item when present.

  it('renders nothing but the footer shell when all optional props are absent', () => {
    render(<AppFooter />);
    expect(screen.queryByText('Legal notice')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /contact/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/respro CLI/)).not.toBeInTheDocument();
    expect(screen.queryByText(/respro WebApp/)).not.toBeInTheDocument();
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
    // First row: GitHub · Fuchs Lab · Legal notice · Contact → 3 separators.
    // Second row: CLI · WebApp → 1 separator.
    const separators = container.querySelectorAll('.app-footer-sep');
    expect(separators).toHaveLength(4);
  });

  it('places a separator between legal and contact when versions are absent', () => {
    const { container } = render(
      <AppFooter legalLink="https://example.org/legal" contactEmail="support@example.org" />,
    );
    // First row: GitHub · Fuchs Lab · Legal notice · Contact → 3 separators.
    const separators = container.querySelectorAll('.app-footer-sep');
    expect(separators).toHaveLength(3);
  });

  it('places no separator between the link and version groups', () => {
    const { container } = render(
      <AppFooter contactEmail="support@example.org" cliVersion="1.2.3" />,
    );
    // First row: GitHub · Fuchs Lab · Contact → 2 separators; second row has
    // a single version span, so no separator there.
    const separators = container.querySelectorAll('.app-footer-sep');
    expect(separators).toHaveLength(2);
  });
});
