import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

import { AboutTab } from './AboutTab';

function renderAbout(props = {}) {
  return render(
    <MemoryRouter initialEntries={['/about']}>
      <AboutTab setActiveMode={() => {}} {...props} />
    </MemoryRouter>,
  );
}

describe('AboutTab structure', () => {
  it('no longer renders hero actions (Start analysis / Take a tour moved to Home)', () => {
    renderAbout({ onStartTour: () => {} });
    expect(screen.queryByRole('button', { name: /start analysis/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /take a tour/i })).toBeNull();
  });

  it('renders a link back to Home', () => {
    renderAbout();
    const link = screen.getByRole('link', { name: /back to home/i });
    expect(link.getAttribute('href')).toBe('/');
  });

  it('renders a table of contents whose anchors resolve to existing section ids', () => {
    renderAbout();
    const toc = screen.getByRole('navigation', { name: /on this page/i });
    const anchors = [...toc.querySelectorAll('a')].map((a) => a.getAttribute('href'));
    expect(anchors.length).toBeGreaterThanOrEqual(4);
    for (const href of anchors) {
      const id = href.replace('#', '');
      expect(document.getElementById(id), href).not.toBeNull();
    }
  });

  it('keeps the technical sections', () => {
    renderAbout();
    expect(screen.getAllByText(/rule nomenclature/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/rule combinations/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/supported interpretation algorithms/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/cli and extended functionality/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/contributing and contact/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/licensing/i).length).toBeGreaterThan(0);
  });

  it('no longer renders the notice cards (moved to Home)', () => {
    renderAbout();
    expect(screen.queryByText(/research use only/i)).toBeNull();
    expect(screen.queryByText(/no database curation/i)).toBeNull();
  });
});

describe('AboutTab contact email', () => {
  // The "Contributing and Contact" card shows a mailto link. When the deployment
  // supplies a contact email via props, that address is used; otherwise the
  // hardcoded maintainer fallback is shown so a contact is always available.

  it('uses the env-sourced contact email when provided', () => {
    renderAbout({ contactEmail: "support@example.org" });
    const link = screen.getByRole('link', { name: 'support@example.org' });
    expect(link).toHaveAttribute('href', 'mailto:support@example.org');
  });

  it('falls back to the hardcoded maintainer address when contactEmail is absent', () => {
    renderAbout();
    const link = screen.getByRole('link', { name: /email jonas fuchs/i });
    expect(link).toHaveAttribute('href', 'mailto:jonas.fuchs@uniklinik-freiburg.de');
  });
});
