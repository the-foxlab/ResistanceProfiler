import { describe, it, expect, vi, beforeAll, afterEach } from 'vitest';
import { render, cleanup, screen } from '@testing-library/react';

// Regression test for the production blank page: the root App component calls
// useDashboardLogic(), which uses useLocation()/useNavigate(). Those hooks must
// be evaluated INSIDE the <BrowserRouter> that App renders, not in the component
// that renders it. In the broken wiring the hooks ran outside the router
// context, so the production bundle crashed with
// "useLocation() may be used only in the context of a <Router> component."
// jsdom unit tests that wrap components in their own MemoryRouter never saw
// this, because they supplied the router context themselves.

// The mocked useDashboardLogic reproduces the real hook's contract: it calls
// router hooks at its top level. If App evaluates it outside <BrowserRouter>,
// mounting throws — exactly the production failure.
vi.mock('./useDashboardLogic', async () => {
  const { useLocation, useNavigate } = await import('react-router');
  return {
    useDashboardLogic: () => {
      const location = useLocation();
      const navigate = useNavigate();
      return {
        activeMode: location.pathname.startsWith('/analysis') ? 'analysis' : 'home',
        navigate,
        setActiveProfileMode: vi.fn(),
        setAnalyzeSubMode: vi.fn(),
      };
    },
  };
});

vi.mock('./components/tour/steps', () => ({
  buildTourSteps: vi.fn(() => []),
}));

let AppRoutesRenderCount = 0;

vi.mock('./AppRoutes', () => ({
  AppRoutes: ({ logic }) => {
    AppRoutesRenderCount += 1;
    return <div data-testid="app-routes">routes:{String(logic.activeMode)}</div>;
  },
}));

vi.mock('./components/AppShell', () => ({
  AppShell: ({ children, onStartTour }) => (
    <div data-testid="app-shell">
      <button type="button" data-testid="shell-tour" onClick={onStartTour}>
        tour
      </button>
      {children}
    </div>
  ),
}));

vi.mock('./hooks/useMobileClass', () => ({
  useMobileClass: () => {},
}));

let App;

describe('root App router context', () => {
  beforeAll(async () => {
    // main.jsx calls createRoot(...).render(...) at module scope, so a #root
    // mount node must exist before the module is imported. Static ESM imports
    // are hoisted above the DOM setup, hence the dynamic import here.
    document.body.innerHTML = '<div id="root"></div>';
    ({ App } = await import('./main.jsx'));
  });

  afterEach(() => {
    cleanup();
    // main.jsx's module-scope createRoot render leaves its own tree in
    // #root, which cleanup() (scoped to render()-created containers) misses.
    document.getElementById('root').innerHTML = '';
    AppRoutesRenderCount = 0;
  });

  it('mounts without a pre-existing Router (hooks live inside BrowserRouter)', () => {
    // Renders <App /> bare — no MemoryRouter wrapper, exactly like index.html.
    // If useDashboardLogic() runs outside the router this throws.
    expect(() => render(<App />)).not.toThrow();
    // StrictMode double-renders in development; assert on the query, not count.
    expect(screen.getAllByTestId('app-routes').length).toBeGreaterThan(0);
  });

  it('renders the app tree exactly once per mount', () => {
    render(<App />);
    expect(AppRoutesRenderCount).toBe(1);
  });

  it('wires the shell help button to the tour context', () => {
    render(<App />);
    expect(screen.getByTestId('shell-tour')).toBeInstanceOf(HTMLButtonElement);
  });
});
