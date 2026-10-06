import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

import { AppShell } from './AppShell';
import { TourProvider, useTour } from './tour/TourContext';

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
        <AppShell logic={logic} onStartTour={props.onStartTour}>
          <div>page-content</div>
        </AppShell>
      </TourProvider>
    </MemoryRouter>,
  );
}

describe('AppShell database selector placement', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('shows the selector on /analysis', () => {
    renderShell('/analysis');
    expect(document.getElementById('topbar-db-select')).not.toBeNull();
  });

  it('shows the selector on /analysis/reports', () => {
    renderShell('/analysis/reports');
    expect(document.getElementById('topbar-db-select')).not.toBeNull();
  });

  it('shows the selector on /databases', () => {
    renderShell('/databases');
    expect(document.getElementById('topbar-db-select')).not.toBeNull();
  });

  it('shows the selector on /databases/mutations', () => {
    renderShell('/databases/mutations');
    expect(document.getElementById('topbar-db-select')).not.toBeNull();
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

  it('shows only the Analysis section under /analysis/*', () => {
    renderShell('/analysis/reports');
    const sections = document.querySelectorAll('.sidebar-rail-section');
    expect(sections).toHaveLength(1);
    expect(sections[0].getAttribute('aria-label')).toBe('Analysis');
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

  it('renders a Launch CTA that navigates to /analysis', () => {
    renderShell('/');
    const launch = screen.getByRole('link', { name: /launch/i });
    expect(launch.getAttribute('href')).toBe('/analysis');
  });

  it('renders a top-bar help button that starts the tour from any route', () => {
    const startTourSpy = vi.fn();
    let tour;
    function TourReader() {
      tour = useTour();
      return null;
    }
    render(
      <MemoryRouter initialEntries={['/databases']}>
        <TourProvider steps={[]}>
          <TourReader />
          <AppShell logic={{ databases: [] }} onStartTour={() => tour.startTour()}>
            <div>page-content</div>
          </AppShell>
        </TourProvider>
      </MemoryRouter>,
    );
    const help = screen.getByRole('button', { name: /start the guided tour/i });
    fireEvent.click(help);
    // The shell delegates to the provider's startTour (active tour, not the coach card).
    expect(tour.isActive).toBe(true);
    expect(startTourSpy).toHaveBeenCalledTimes(0);
  });

  it('the help button is reachable from a non-analysis route (no tour UI shown there)', () => {
    const onStartTour = vi.fn();
    renderShell('/databases', { onStartTour });
    expect(screen.getByRole('button', { name: /start the guided tour/i })).toBeInTheDocument();
    expect(document.querySelector('.tour-coach-card')).toBeNull();
  });

  it('renders breadcrumbs reflecting the route', () => {
    renderShell('/databases/mutations');
    const nav = screen.getByRole('navigation', { name: /breadcrumb/i });
    expect(nav.textContent).toContain('Databases');
    expect(nav.textContent).toContain('Browse Mutations');
  });

  it('renders a skip link that targets the main content region', () => {
    renderShell('/analysis');
    const skip = screen.getByRole('link', { name: /skip to (main )?content/i });
    expect(skip.getAttribute('href')).toBe('#main-content');
    const main = document.querySelector('main.dashboard-shell');
    expect(main.getAttribute('id')).toBe('main-content');
  });
});

describe('AppShell mobile drawer', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('toggles the drawer and closes it on backdrop click and Escape', () => {
    renderShell('/analysis');
    const btn = screen.getByRole('button', { name: /toggle navigation/i });
    fireEvent.click(btn);
    expect(document.getElementById('sidebar-rail')).toHaveClass('open');
    fireEvent.click(document.querySelector('.mobile-nav-backdrop'));
    expect(document.getElementById('sidebar-rail')).not.toHaveClass('open');
    fireEvent.click(btn);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(document.getElementById('sidebar-rail')).not.toHaveClass('open');
  });
});
