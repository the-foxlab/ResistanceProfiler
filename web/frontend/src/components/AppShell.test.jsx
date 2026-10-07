import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

import { AppShell } from './AppShell';
import { TourProvider } from './tour/TourContext';

function renderShell(path, props = {}) {
  const logic = {
    databases: [{ id: 'db1', display_name: 'DB One' }],
    selectedDatabase: { id: 'db1', display_name: 'DB One' },
    selectedDatabaseId: 'db1',
    setSelectedDatabaseId: vi.fn(),
    legalLink: null,
    contactEmail: null,
    cliVersion: null,
    webVersion: '0.0.0',
    ...props,
  };
  return render(
    <MemoryRouter initialEntries={[path]}>
      <TourProvider steps={[]}>
        <AppShell logic={logic}>
          <div>page-content</div>
        </AppShell>
      </TourProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('AppShell database selector placement', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('shows the selector on /analysis', () => {
    renderShell('/analysis');
    const selector = document.getElementById('topbar-db-select');
    expect(selector).not.toBeNull();
    expect(selector.closest('.workspace-subheader')).not.toBeNull();
    expect(selector.closest('.top-bar-actions')).toBeNull();
  });

  it('places the Example action directly after the database selector on /analysis', () => {
    const setActiveProfileMode = vi.fn();
    const setAnalyzeSubMode = vi.fn();
    const runExampleProfile = vi.fn();
    renderShell('/analysis', {
      databases: [{ id: 'db1', display_name: 'DB One', has_example: true }],
      selectedDatabase: { id: 'db1', display_name: 'DB One', has_example: true },
      setActiveProfileMode,
      setAnalyzeSubMode,
      runExampleProfile,
    });

    const selector = document.getElementById('topbar-db-select');
    const exampleButton = screen.getByRole('button', { name: 'Example' });
    expect(selector.nextElementSibling).toBe(exampleButton);
    fireEvent.click(exampleButton);
    expect(setActiveProfileMode).toHaveBeenCalledWith('fasta');
    expect(setAnalyzeSubMode).toHaveBeenCalledWith('single');
    expect(runExampleProfile).toHaveBeenCalledOnce();
  });

  it('does not show the Example action on other database-selector pages', () => {
    renderShell('/databases', {
      databases: [{ id: 'db1', display_name: 'DB One', has_example: true }],
      selectedDatabase: { id: 'db1', display_name: 'DB One', has_example: true },
    });
    expect(screen.queryByRole('button', { name: 'Example' })).toBeNull();
  });

  it('shows the selector on /analysis/reports', () => {
    renderShell('/analysis/reports');
    expect(document.getElementById('topbar-db-select')).toBeNull();
  });

  it('shows the selector on /databases', () => {
    renderShell('/databases');
    expect(document.getElementById('topbar-db-select')).not.toBeNull();
  });

  it('shows the selector on /databases/mutations', () => {
    renderShell('/databases/mutations');
    expect(document.getElementById('topbar-db-select')).not.toBeNull();
  });

  it.each([
    ['/analysis', 'Analyze'],
    ['/analysis/reports', 'Session results'],
    ['/databases', 'Database Dashboard'],
    ['/databases/mutations', 'Browse mutations'],
    ['/databases/compare', 'Compare databases'],
  ])('places the %s heading in the shared workspace row', (path, headingText) => {
    renderShell(path);
    const heading = screen.getByRole('heading', { level: 1, name: headingText });
    expect(heading.closest('.workspace-subheader')).not.toBeNull();
    expect(screen.queryByRole('heading', { level: 2, name: headingText })).toBeNull();
  });

  it('hides the selector on /', () => {
    renderShell('/');
    expect(document.getElementById('topbar-db-select')).toBeNull();
  });

  it('hides the selector on /about', () => {
    renderShell('/about');
    expect(document.getElementById('topbar-db-select')).toBeNull();
  });

  it('hides the selector on /databases/compare (tab has its own selectors)', () => {
    renderShell('/databases/compare');
    expect(document.getElementById('topbar-db-select')).toBeNull();
  });
});

describe('AppShell sidebar visibility', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders the sidebar on workspace routes', () => {
    for (const path of ['/analysis', '/analysis/reports', '/databases', '/databases/mutations', '/databases/compare']) {
      const { unmount } = renderShell(path);
      expect(document.getElementById('sidebar-rail'), path).not.toBeNull();
      unmount();
    }
  });

  it('does not render the sidebar on Home or About', () => {
    for (const path of ['/', '/about']) {
      const { unmount } = renderShell(path);
      expect(document.getElementById('sidebar-rail'), path).toBeNull();
      unmount();
    }
  });

  it('moves the sidebar nav with top-bar visibility', () => {
    let reportIntersection;
    vi.stubGlobal('IntersectionObserver', class {
      constructor(callback) {
        reportIntersection = callback;
      }

      observe() {}

      disconnect() {}
    });

    renderShell('/analysis');
    const sidebar = document.getElementById('sidebar-rail');
    expect(sidebar).not.toHaveClass('header-hidden');

    act(() => reportIntersection([{ isIntersecting: false, intersectionRatio: 0 }]));
    expect(sidebar).toHaveClass('header-hidden');

    fireEvent.click(screen.getByRole('link', { name: 'Reports' }));
    expect(sidebar).toHaveClass('header-hidden');

    act(() => reportIntersection([{ isIntersecting: true, intersectionRatio: 0.1 }]));
    expect(sidebar).toHaveClass('header-hidden');

    act(() => reportIntersection([{ isIntersecting: true, intersectionRatio: 0.6 }]));
    expect(sidebar).not.toHaveClass('header-hidden');
  });

  it('shows only the Analysis section under /analysis/*', () => {
    renderShell('/analysis/reports');
    const sections = document.querySelectorAll('.sidebar-rail-section');
    expect(sections).toHaveLength(1);
    expect(sections[0].getAttribute('aria-label')).toBe('Analysis');
    expect(document.querySelector('.sidebar-rail-divider')).toBeNull();
  });

  it('shows only the Databases section under /databases/*', () => {
    renderShell('/databases/mutations');
    const sections = document.querySelectorAll('.sidebar-rail-section');
    expect(sections).toHaveLength(1);
    expect(sections[0].getAttribute('aria-label')).toBe('Databases');
  });

  it('persists collapse state in localStorage', () => {
    renderShell('/analysis');
    const toggle = screen.getByRole('button', { name: /collapse sidebar/i });
    fireEvent.click(toggle);
    expect(localStorage.getItem('respro.sidebar.collapsed')).toBe('1');
    expect(document.getElementById('sidebar-rail')).toHaveClass('collapsed');
    fireEvent.click(toggle);
    expect(localStorage.getItem('respro.sidebar.collapsed')).toBe('0');
  });

  it('opens mobile navigation as an overlay and closes it from the backdrop', () => {
    renderShell('/analysis');
    const sidebar = document.getElementById('sidebar-rail');
    const dashboardMain = document.querySelector('.dashboard-main');
    const toggle = screen.getByRole('button', { name: /open sidebar/i });

    fireEvent.click(toggle);
    expect(sidebar).toHaveClass('mobile-open');
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(document.querySelector('.mobile-sidebar-backdrop')).not.toBeNull();
    expect(document.querySelector('.dashboard-main')).toBe(dashboardMain);

    fireEvent.click(screen.getByRole('button', { name: /close navigation/i }));
    expect(sidebar).not.toHaveClass('mobile-open');
    expect(document.querySelector('.mobile-sidebar-backdrop')).toBeNull();
  });

  it('keeps text labels in the mobile drawer when desktop collapse is saved', () => {
    localStorage.setItem('respro.sidebar.collapsed', '1');
    renderShell('/analysis');
    const sidebar = document.getElementById('sidebar-rail');

    fireEvent.click(screen.getByRole('button', { name: /open sidebar/i }));

    expect(sidebar).toHaveClass('collapsed', 'mobile-open');
    expect(sidebar.querySelector('.sidebar-rail-text')).toHaveTextContent('Analyze');
    expect(sidebar.querySelectorAll('.sidebar-rail-text')[1]).toHaveTextContent('Reports');
  });
});

describe('AppShell top bar navigation', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders primary nav links with aria-current on the active route', () => {
    renderShell('/analysis');
    const link = screen.getByRole('link', { name: 'Analysis' });
    expect(link).toHaveAttribute('aria-current', 'page');
  });

  it('keeps the primary navigation without redundant top-bar actions', () => {
    renderShell('/');
    expect(screen.getByRole('navigation', { name: /primary/i })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /^launch$/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /guided tour/i })).toBeNull();
  });

  it('does not render redundant breadcrumb navigation', () => {
    renderShell('/databases/mutations');
    expect(screen.queryByRole('navigation', { name: /breadcrumb/i })).toBeNull();
  });

  it('renders a skip link that targets the main content region', () => {
    renderShell('/analysis');
    const skip = screen.getByRole('link', { name: /skip to (main )?content/i });
    expect(skip.getAttribute('href')).toBe('#main-content');
    const main = document.querySelector('main.dashboard-shell');
    expect(main.getAttribute('id')).toBe('main-content');
  });
});

describe('AppShell compact sidebar', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('keeps the collapsible sidebar available without a hamburger drawer', () => {
    renderShell('/analysis');
    expect(screen.queryByRole('button', { name: /toggle navigation/i })).toBeNull();
    expect(document.querySelector('.mobile-nav-backdrop')).toBeNull();
    expect(document.getElementById('sidebar-rail')).not.toHaveClass('open');
    expect(screen.getByRole('button', { name: /collapse sidebar/i })).toBeInTheDocument();
  });
});
