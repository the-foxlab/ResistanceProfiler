import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
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

  it('does not render a Back to home link', () => {
    renderAbout();
    expect(screen.queryByRole('link', { name: /back to home/i })).toBeNull();
  });

  it('shows only the selected section and includes funding navigation', () => {
    renderAbout();
    const toc = screen.getByRole('navigation', { name: /on this page/i });
    expect(within(toc).getAllByRole('button')).toHaveLength(8);
    expect(document.getElementById('about-scope')).not.toBeNull();
    expect(document.getElementById('about-cli')).toBeNull();

    fireEvent.click(within(toc).getByRole('button', { name: 'Funding' }));

    const fundingHeading = screen.getByRole('heading', { name: 'Supported by' });
    expect(fundingHeading.closest('section')).toHaveClass('about-section-card');
    expect(document.getElementById('about-scope')).toBeNull();
    expect(document.getElementById('about-funding')).not.toBeNull();
  });

  it('keeps the technical sections', () => {
    renderAbout();
    const toc = screen.getByRole('navigation', { name: /on this page/i });
    const sections = [
      ['Rule nomenclature', /rule nomenclature basics/i],
      ['Rule combinations', /rule combinations/i],
      ['Interpretation algorithms', /supported interpretation algorithms/i],
      ['CLI and extended functionality', /cli and extended functionality/i],
      ['Contributing, data usage, licensing', /contributing, data use and licensing/i],
    ];

    for (const [label, heading] of sections) {
      fireEvent.click(within(toc).getByRole('button', { name: label }));
      expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument();
    }
  });

  it('no longer renders the notice cards (moved to Home)', () => {
    renderAbout();
    expect(screen.queryByText(/research use only/i)).toBeNull();
    expect(screen.queryByText(/no database curation/i)).toBeNull();
  });

  it('renders a Databases section with the respro-databases link', () => {
    renderAbout();
    const toc = screen.getByRole('navigation', { name: /on this page/i });
    fireEvent.click(within(toc).getByRole('button', { name: 'Databases' }));

    expect(screen.getByRole('heading', { name: 'Databases' })).toBeInTheDocument();
    const link = screen.getByRole('link', { name: 'Maintained database repository' });
    expect(link).toHaveAttribute('href', 'https://github.com/the-foxlab/respro-databases');
  });

  it('keeps contributing, data use, and licensing in one tile', () => {
    renderAbout();
    const toc = screen.getByRole('navigation', { name: /on this page/i });
    fireEvent.click(within(toc).getByRole('button', { name: 'Contributing, data usage, licensing' }));

    const section = document.getElementById('about-governance');
    expect(section).toHaveClass('about-section-card');
    expect(within(section).getByRole('heading', { level: 3, name: /contributing, data use and licensing/i })).toBeInTheDocument();
    expect(within(section).getAllByRole('heading', { level: 4 })).toHaveLength(3);
    expect(within(section).getByText(/24 hours by default/i)).toBeInTheDocument();
    expect(within(section).queryByText(/no data is stored on remote servers/i)).toBeNull();
  });

  it('ends sections with links to their relevant documentation', () => {
    renderAbout();
    const toc = screen.getByRole('navigation', { name: /on this page/i });
    const docSections = [
      ['Project scope and how it works', 'Pipeline overview and reference matching', /how-it-works\/#pipeline-overview$/],
      ['Rule nomenclature', 'Mutation notation and normalization', /rules-format\/#normalization-examples-input-canonical-interpretation$/],
      ['Rule combinations', 'Combination rule matching', /how-it-works\/#rule-matching$/],
      ['Interpretation algorithms', 'Interpretation algorithms', /algorithms\/#how-respro-evaluates-resistance$/],
      ['Databases', 'Create a project database', /database-preparation\/#create-a-new-project-database$/],
      ['CLI and extended functionality', 'FASTA profiling command', /cli-reference\/#profile-fasta-input$/],
      ['Contributing, data usage, licensing', 'Web-app data storage and retention', /webapp\/#data-and-filesystem$/],
      ['Funding', 'Hans A. Krebs Program information', /medical-scientist\/$/],
    ];

    for (const [sectionLabel, linkLabel, href] of docSections) {
      fireEvent.click(within(toc).getByRole('button', { name: sectionLabel }));
      const link = screen.getByRole('link', { name: linkLabel });
      expect(link.getAttribute('href')).toMatch(href);
      if (sectionLabel === 'Rule nomenclature') {
        const section = screen.getByRole('heading', { name: /rule nomenclature basics/i }).closest('article');
        expect(within(section).getByRole('heading', { name: 'Phenotype ranks and accepted labels' })).toBeInTheDocument();
        expect(within(section).getByText('potential low-level resistance')).toBeInTheDocument();
        expect(within(section).getByText('contradictory')).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'Phenotype rank vocabulary' })).toHaveAttribute(
          'href',
          'https://the-foxlab.github.io/ResistanceProfiler/rules-format/#phenotype-normalization',
        );
      }
    }
  });
});

describe('AboutTab contact email', () => {
  // The "Contributing and Contact" card shows a mailto link. When the deployment
  // supplies a contact email via props, that address is used; otherwise the
  // hardcoded maintainer fallback is shown so a contact is always available.

  it('uses the env-sourced contact email when provided', () => {
    renderAbout({ contactEmail: "support@example.org" });
    const toc = screen.getByRole('navigation', { name: /on this page/i });
    fireEvent.click(within(toc).getByRole('button', { name: 'Contributing, data usage, licensing' }));
    const link = screen.getByRole('link', { name: 'support@example.org' });
    expect(link).toHaveAttribute('href', 'mailto:support@example.org');
  });

  it('falls back to the hardcoded maintainer address when contactEmail is absent', () => {
    renderAbout();
    const toc = screen.getByRole('navigation', { name: /on this page/i });
    fireEvent.click(within(toc).getByRole('button', { name: 'Contributing, data usage, licensing' }));
    const link = screen.getByRole('link', { name: /jonas fuchs/i });
    expect(link).toHaveAttribute('href', 'mailto:jonas.fuchs@uniklinik-freiburg.de');
  });
});
