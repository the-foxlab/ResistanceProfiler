import fs from 'node:fs';
import path from 'node:path';
import { afterEach, describe, it, expect, vi } from 'vitest';
import { act, cleanup, render, screen, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

import { AboutPage } from './AboutPage';

function renderAbout(logic = {}) {
  return render(
    <MemoryRouter initialEntries={['/about']}>
      <AboutPage logic={logic} />
    </MemoryRouter>,
  );
}

describe('AboutPage structure', () => {
  it('no longer renders hero actions (Start analysis / Take a tour moved to Home)', () => {
    renderAbout();
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
    expect(screen.getByText(/Institute of Virology Freiburg/)).toBeInTheDocument();
    expect(screen.queryByText(/Department of Virology at the Medical Center – Universityclinic of Freiburg/)).toBeNull();
    expect(document.getElementById('about-scope')).toBeNull();
    expect(document.getElementById('about-funding')).not.toBeNull();

    const virologyLogo = screen.getByRole('img', {
      name: 'Institute of Virology, Medical Center – University of Freiburg logo',
    });
    expect(virologyLogo).toHaveAttribute(
      'src',
      'https://www.uniklinik-freiburg.de/fileadmin/_processed_/9/1/csm_Virologie_D__2021_1aa65478be.png',
    );
    expect(virologyLogo).toHaveAttribute('referrerpolicy', 'no-referrer');
    const krebsLogo = screen.getByRole('img', {
      name: 'Hans A. Krebs Program for Medical Scientists logo',
    });
    expect(krebsLogo).toHaveAttribute('referrerpolicy', 'no-referrer');
    expect(virologyLogo.closest('a')).toHaveAttribute(
      'href',
      'https://www.uniklinik-freiburg.de/virologie-en/research/research-teams/jonas-fuchs-team.html',
    );
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
    expect(within(section).getByText(/uploaded files and generated results are stored temporarily and are removed as soon as you close the session/i)).toBeInTheDocument();
    expect(within(section).getByText(/© 2026 Jonas Fuchs, University Medical Center Freiburg/i)).toBeInTheDocument();
    expect(within(section).getByRole('link', { name: 'full license' })).toHaveAttribute(
      'href',
      'https://github.com/the-foxlab/ResistanceProfiler/blob/master/LICENSE',
    );
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

describe('AboutPage contact email', () => {
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

  it('sanitizes contactEmail and falls back to maintainer address on mailto injection payloads', () => {
    renderAbout({ contactEmail: 'evil@x.org?bcc=attacker@y.org' });
    const toc = screen.getByRole('navigation', { name: /on this page/i });
    fireEvent.click(within(toc).getByRole('button', { name: 'Contributing, data usage, licensing' }));
    const link = screen.getByRole('link', { name: 'Jonas Fuchs' });
    expect(link).toHaveAttribute('href', 'mailto:jonas.fuchs@uniklinik-freiburg.de');
    expect(screen.queryByRole('link', { name: /evil@x\.org/i })).toBeNull();
  });
});

describe('CSP theme-init script and index.html', () => {
  it('loads /theme-init.js externally in index.html without any inline script body', () => {
    const indexPath = path.resolve(import.meta.dirname, '../../../index.html');
    const themeInitPath = path.resolve(import.meta.dirname, '../../../public/theme-init.js');
    const html = fs.readFileSync(indexPath, 'utf8');
    const script = fs.readFileSync(themeInitPath, 'utf8');

    const scriptTags = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)];
    expect(scriptTags.length).toBe(2);
    for (const [, attrs, body] of scriptTags) {
      expect(attrs).toMatch(/\bsrc="/);
      expect(body.trim()).toBe('');
    }
    expect(html).toContain('<script src="/theme-init.js"></script>');

    const origMatchMedia = window.matchMedia;
    const origGetItem = Storage.prototype.getItem;
    try {
      localStorage.setItem('respro-theme', 'dark');
      window.eval(script);
      expect(document.documentElement.getAttribute('data-theme')).toBe('dark');

      localStorage.setItem('respro-theme', 'light');
      window.eval(script);
      expect(document.documentElement.getAttribute('data-theme')).toBe('light');

      localStorage.removeItem('respro-theme');
      window.matchMedia = vi.fn(() => ({ matches: true }));
      window.eval(script);
      expect(document.documentElement.getAttribute('data-theme')).toBe('dark');

      Storage.prototype.getItem = () => {
        throw new Error('storage blocked');
      };
      window.eval(script);
      expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    } finally {
      window.matchMedia = origMatchMedia;
      Storage.prototype.getItem = origGetItem;
      localStorage.removeItem('respro-theme');
      document.documentElement.removeAttribute('data-theme');
    }
  });
});

describe('AboutPage copy-citation timeout lifecycle (WA-050)', () => {
  const originalNavigator = global.navigator;

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.stubGlobal('navigator', originalNavigator);
  });

  function openCliSection() {
    const view = renderAbout();
    const toc = screen.getByRole('navigation', { name: /on this page/i });
    fireEvent.click(within(toc).getByRole('button', { name: 'CLI and extended functionality' }));
    return view;
  }

  it('shows Copied and resets back to Copy after 1600ms', async () => {
    vi.useFakeTimers();
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });

    openCliSection();
    const copyBtn = screen.getByRole('button', { name: 'Copy CLI commands' });

    await act(async () => {
      fireEvent.click(copyBtn);
      await Promise.resolve();
    });
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(copyBtn).toHaveTextContent('Copied');

    act(() => {
      vi.advanceTimersByTime(1599);
    });
    expect(copyBtn).toHaveTextContent('Copied');

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(copyBtn).toHaveTextContent('Copy');
  });

  it('clears the pending copy timeout on unmount without warnings', async () => {
    vi.useFakeTimers();
    const clearTimeoutSpy = vi.spyOn(globalThis, 'clearTimeout');
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });

    const { unmount } = openCliSection();
    const copyBtn = screen.getByRole('button', { name: 'Copy CLI commands' });

    await act(async () => {
      fireEvent.click(copyBtn);
      await Promise.resolve();
    });
    expect(copyBtn).toHaveTextContent('Copied');

    unmount();
    expect(clearTimeoutSpy).toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it('cancels the prior timer on rapid re-trigger so Copied stays visible for a full 1600ms', async () => {
    vi.useFakeTimers();
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });

    openCliSection();
    const copyBtn = screen.getByRole('button', { name: 'Copy CLI commands' });

    await act(async () => {
      fireEvent.click(copyBtn);
      await Promise.resolve();
    });
    expect(copyBtn).toHaveTextContent('Copied');

    act(() => {
      vi.advanceTimersByTime(1000);
    });

    await act(async () => {
      fireEvent.click(copyBtn);
      await Promise.resolve();
    });
    expect(copyBtn).toHaveTextContent('Copied');

    // t=1600ms from first click (600ms after second click) must still show Copied.
    act(() => {
      vi.advanceTimersByTime(600);
    });
    expect(copyBtn).toHaveTextContent('Copied');

    // t=1600ms from second click resets to Copy.
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(copyBtn).toHaveTextContent('Copy');
  });

  it('does not schedule a timer or update state if unmounted while clipboard write is pending', async () => {
    vi.useFakeTimers();
    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout');
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    let resolveWrite;
    let rejectWrite;
    const writeText = vi.fn()
      .mockImplementationOnce(() => new Promise((res) => { resolveWrite = res; }))
      .mockImplementationOnce(() => new Promise((_, rej) => { rejectWrite = rej; }));
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });

    const first = openCliSection();
    fireEvent.click(screen.getByRole('button', { name: 'Copy CLI commands' }));
    const timeoutsBeforeResolve = setTimeoutSpy.mock.calls.length;
    first.unmount();

    await act(async () => {
      resolveWrite();
      await Promise.resolve();
    });
    expect(setTimeoutSpy.mock.calls.length).toBe(timeoutsBeforeResolve);
    expect(errorSpy).not.toHaveBeenCalled();

    const second = openCliSection();
    fireEvent.click(screen.getByRole('button', { name: 'Copy CLI commands' }));
    second.unmount();

    await act(async () => {
      rejectWrite(new Error('denied'));
      await Promise.resolve();
    });
    expect(errorSpy).not.toHaveBeenCalled();
  });
});
