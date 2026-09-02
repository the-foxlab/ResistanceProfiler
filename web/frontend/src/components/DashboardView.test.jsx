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
});
