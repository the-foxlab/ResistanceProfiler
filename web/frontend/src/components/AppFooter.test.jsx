import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { AppFooter } from './AppFooter';

describe('AppFooter', () => {
  // The footer renders optional Legal notice and Contact links plus version spans.
  // Each neighbour pair is separated by a `·` only when both are present, so the
  // separator count equals the number of adjacent present items minus one.

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
    const separators = container.querySelectorAll('.app-footer-sep');
    expect(separators).toHaveLength(2);
  });

  it('places a separator between legal and contact when versions are absent', () => {
    const { container } = render(
      <AppFooter legalLink="https://example.org/legal" contactEmail="support@example.org" />,
    );
    const separators = container.querySelectorAll('.app-footer-sep');
    expect(separators).toHaveLength(1);
  });

  it('places no separator between the link and version groups', () => {
    const { container } = render(
      <AppFooter contactEmail="support@example.org" cliVersion="1.2.3" />,
    );
    const separators = container.querySelectorAll('.app-footer-sep');
    expect(separators).toHaveLength(0);
  });
});
