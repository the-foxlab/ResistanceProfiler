import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { DashboardView } from './DashboardView';
import { TourProvider } from './tour/TourContext';

function renderWithTour(ui) {
  return render(<TourProvider steps={[]}>{ui}</TourProvider>);
}
// Minimal prop set: DashboardView destructures many props, but only a few are
// read in the sidebar/top-bar region exercised here. Provide stubs for the
// rest so the component renders without throwing.
function minimalProps(overrides = {}) {
  return {
    PROFILE_MODES: [],
    databases: [],
    selectedDatabase: null,
    selectedDatabaseId: '',
    setSelectedDatabaseId: () => {},
    activeMode: 'analyze',
    setActiveMode: vi.fn(),
    activeProfileMode: '',
    setActiveProfileMode: () => {},
    analyzeSubMode: '',
    setAnalyzeSubMode: () => {},
    reportOptions: [],
    sessionResults: [],
    mutationColumns: [],
    formulaColumns: [],
    mutationPlotMeta: [],
    displayedRules: [],
    displayedFormulaRules: [],
    rules: [],
    formulaRules: [],
    uploadProgress: { percent: 0, name: '' },
    ...overrides,
  };
}

describe('DashboardView mobile sidebar toggle', () => {
  it('renders a hamburger button with aria-expanded=false initially', () => {
    renderWithTour(<DashboardView {...minimalProps()} />);
    const btn = screen.getByRole('button', { name: /toggle navigation/i });
    expect(btn).toBeInTheDocument();
    expect(btn.getAttribute('aria-expanded')).toBe('false');
    expect(btn).not.toHaveClass('open');
  });

  it('toggles the sidebar open class and aria-expanded on click', () => {
    renderWithTour(<DashboardView {...minimalProps()} />);
    const btn = screen.getByRole('button', { name: /toggle navigation/i });
    fireEvent.click(btn);
    expect(btn.getAttribute('aria-expanded')).toBe('true');
    expect(btn).toHaveClass('open');
    const rail = document.getElementById('sidebar-rail');
    expect(rail).toHaveClass('open');
    // Close again.
    fireEvent.click(btn);
    expect(btn.getAttribute('aria-expanded')).toBe('false');
    expect(rail).not.toHaveClass('open');
  });

  it('closes the sidebar and forwards setActiveMode when a mode is selected', () => {
    const setActiveMode = vi.fn();
    renderWithTour(<DashboardView {...minimalProps({ setActiveMode })} />);
    // Open the drawer first.
    const btn = screen.getByRole('button', { name: /toggle navigation/i });
    fireEvent.click(btn);
    expect(document.getElementById('sidebar-rail')).toHaveClass('open');
    // Click the "Reports" mode link.
    const reportsBtn = screen.getByRole('button', { name: /reports/i });
    fireEvent.click(reportsBtn);
    expect(setActiveMode).toHaveBeenCalledWith('results');
    expect(document.getElementById('sidebar-rail')).not.toHaveClass('open');
  });

  it('renders a backdrop overlay only when the drawer is open', () => {
    renderWithTour(<DashboardView {...minimalProps()} />);
    // The backdrop element is always in the DOM but hidden via CSS until open.
    const backdrop = document.querySelector('.mobile-nav-backdrop');
    expect(backdrop).toBeInTheDocument();
    expect(backdrop).not.toHaveClass('is-open');
    // Open the drawer: backdrop becomes active.
    fireEvent.click(screen.getByRole('button', { name: /toggle navigation/i }));
    expect(backdrop).toHaveClass('is-open');
    expect(backdrop).toHaveAttribute('aria-hidden', 'true');
  });

  it('closes the sidebar when the backdrop is clicked', () => {
    renderWithTour(<DashboardView {...minimalProps()} />);
    fireEvent.click(screen.getByRole('button', { name: /toggle navigation/i }));
    expect(document.getElementById('sidebar-rail')).toHaveClass('open');
    fireEvent.click(document.querySelector('.mobile-nav-backdrop'));
    expect(document.getElementById('sidebar-rail')).not.toHaveClass('open');
  });

  it('closes the sidebar on Escape when open', () => {
    renderWithTour(<DashboardView {...minimalProps()} />);
    fireEvent.click(screen.getByRole('button', { name: /toggle navigation/i }));
    expect(document.getElementById('sidebar-rail')).toHaveClass('open');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(document.getElementById('sidebar-rail')).not.toHaveClass('open');
  });
});

describe('DashboardView sidebar sections', () => {
  it('groups analysis and database modes into two labelled sections', () => {
    renderWithTour(<DashboardView {...minimalProps()} />);
    const sections = document.querySelectorAll('.sidebar-rail-section');
    expect(sections).toHaveLength(2);
    expect(sections[0].getAttribute('aria-label')).toBe('Analysis and reports');
    expect(sections[1].getAttribute('aria-label')).toBe('Database browsing');
  });

  it('places analysis modes in the first section and database modes in the second', () => {
    renderWithTour(<DashboardView {...minimalProps()} />);
    const first = document.querySelectorAll('.sidebar-rail-section')[0];
    const second = document.querySelectorAll('.sidebar-rail-section')[1];
    const firstIds = [...first.querySelectorAll('.sidebar-rail-link')].map((b) => b.getAttribute('aria-label'));
    const secondIds = [...second.querySelectorAll('.sidebar-rail-link')].map((b) => b.getAttribute('aria-label'));
    expect(firstIds).toEqual(['Analysis', 'Reports']);
    expect(secondIds).toEqual(['Database Dashboard', 'Browse Mutations', 'Compare Databases']);
  });

  it('separates the sections with a subtle divider', () => {
    renderWithTour(<DashboardView {...minimalProps()} />);
    const dividers = document.querySelectorAll('.sidebar-rail-divider');
    expect(dividers.length).toBeGreaterThanOrEqual(1);
  });

  it('keeps About pinned below the sections, outside them', () => {
    renderWithTour(<DashboardView {...minimalProps()} />);
    const sections = document.querySelectorAll('.sidebar-rail-section');
    for (const section of sections) {
      const labels = [...section.querySelectorAll('.sidebar-rail-link')].map((b) => b.getAttribute('aria-label'));
      expect(labels).not.toContain('About');
    }
    expect(screen.getByRole('button', { name: 'About' })).toBeInTheDocument();
  });

  it('still forwards setActiveMode when a mode inside a section is clicked', () => {
    const setActiveMode = vi.fn();
    renderWithTour(<DashboardView {...minimalProps({ setActiveMode })} />);
    fireEvent.click(screen.getByRole('button', { name: /compare databases/i }));
    expect(setActiveMode).toHaveBeenCalledWith('compare');
  });
});
