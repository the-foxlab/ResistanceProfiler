import { describe, it, expect, vi, beforeAll, afterEach } from 'vitest';
import { act, render, cleanup, screen, fireEvent } from '@testing-library/react';

// Regression test for the production blank page: the root App component mounts
// DashboardProvider, which uses useNavigate(). Those router hooks must
// be evaluated INSIDE the <BrowserRouter> that App renders, not in the component
// that renders it. In the broken wiring the hooks ran outside the router
// context, so the production bundle crashed with
// "useLocation() may be used only in the context of a <Router> component."
// jsdom unit tests that wrap components in their own MemoryRouter never saw
// this, because they supplied the router context themselves.

// Mock DashboardContext with a lightweight provider that calls useNavigate()
// inside BrowserRouter (preserving the real router-context contract) while
// stubbing out network startup fetches.
vi.mock('./DashboardContext', async () => {
  const { createContext, useCallback, useContext, useMemo } = await import('react');
  const { useNavigate } = await import('react-router');
  const Ctx = createContext(null);
  return {
    DashboardProvider: ({ children }) => {
      const navigate = useNavigate();
      const setActiveProfileMode = useCallback(() => {}, []);
      const setAnalyzeSubMode = useCallback(() => {}, []);
      const value = useMemo(
        () => ({ navigate, setActiveProfileMode, setAnalyzeSubMode }),
        [navigate, setActiveProfileMode, setAnalyzeSubMode],
      );
      return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
    },
    useCoreActions: () => {
      const ctx = useContext(Ctx);
      if (!ctx) throw new Error('useCoreActions must be used within DashboardProvider');
      return ctx;
    },
    useOptionalShellLogic: () => ({
      databases: [],
      selectedDatabase: null,
      selectedDatabaseId: '',
      setSelectedDatabaseId: () => {},
      legalLink: null,
      contactEmail: null,
      cliVersion: null,
      webVersion: '0.0.0',
      setActiveProfileMode: () => {},
      setAnalyzeSubMode: () => {},
      runExampleProfile: () => {},
      isProfileBusy: false,
      isRegenerateBusy: false,
      isUploading: false,
      batchSubmitting: false,
      isBatchUploading: false,
    }),
  };
});

const mockedBuildTourSteps = vi.fn(() => [
  { id: 'step-1', targetSelector: '.top-bar', title: 'Step 1', body: 'Intro' },
]);

vi.mock('./components/tour/steps', () => ({
  buildTourSteps: (...args) => mockedBuildTourSteps(...args),
}));

let AppRoutesRenderCount = 0;
let shouldThrowInRoutes = false;

vi.mock('./AppRoutes', async () => {
  const { useOptionalTourActions } = await import('./components/tour/TourContext');
  return {
    AppRoutes: ({ logic }) => {
      if (shouldThrowInRoutes) {
        throw new Error('simulated route crash');
      }
      AppRoutesRenderCount += 1;
      const tourActions = useOptionalTourActions();
      return (
        <div data-testid="app-routes" data-has-logic-prop={String(logic !== undefined)}>
          <button
            type="button"
            data-testid="routes-start-tour"
            onClick={() => tourActions?.startTour()}
          >
            start
          </button>
        </div>
      );
    },
  };
});

vi.mock('./components/AppShell', async () => {
  const { useTour } = await import('./components/tour/TourContext');
  return {
    AppShell: ({ logic, children }) => {
      const { isActive, stepIndex } = useTour();
      return (
        <div data-testid="app-shell" data-has-logic-prop={String(logic !== undefined)}>
          <span data-testid="tour-status">
            {isActive ? `active:${stepIndex}` : 'inactive'}
          </span>
          {children}
        </div>
      );
    },
  };
});

let App;

describe('root App router context', () => {
  beforeAll(async () => {
    // main.jsx calls createRoot(...).render(...) at module scope, so a #root
    // mount node must exist before the module is imported. Static ESM imports
    // are hoisted above the DOM setup, hence the dynamic import here.
    document.body.innerHTML = '<div id="root"></div>';
    await act(async () => {
      ({ App } = await import('./main.jsx'));
    });
  });

  afterEach(() => {
    cleanup();
    // main.jsx's module-scope createRoot render leaves its own tree in
    // #root, which cleanup() (scoped to render()-created containers) misses.
    document.getElementById('root').innerHTML = '';
    AppRoutesRenderCount = 0;
    shouldThrowInRoutes = false;
    mockedBuildTourSteps.mockClear();
    vi.restoreAllMocks();
  });

  it('mounts without a pre-existing Router (DashboardProvider hooks live inside BrowserRouter)', () => {
    // Renders <App /> bare — no MemoryRouter wrapper, exactly like index.html.
    // If DashboardProvider runs outside the router this throws.
    expect(() => render(<App />)).not.toThrow();
    // StrictMode double-renders in development; assert on the query, not count.
    expect(screen.getAllByTestId('app-routes').length).toBeGreaterThan(0);
    expect(screen.getAllByTestId('app-shell')[0]).toHaveAttribute('data-has-logic-prop', 'false');
    expect(screen.getAllByTestId('app-routes')[0]).toHaveAttribute('data-has-logic-prop', 'false');
  });

  it('renders the app tree exactly once per mount', () => {
    render(<App />);
    expect(AppRoutesRenderCount).toBe(1);
  });

  it('wires buildTourSteps with CoreActions and connects TourActionsContext startTour inside TourProvider', () => {
    render(<App />);
    expect(mockedBuildTourSteps).toHaveBeenCalledWith(
      expect.objectContaining({
        navigate: expect.any(Function),
        setActiveProfileMode: expect.any(Function),
        setAnalyzeSubMode: expect.any(Function),
      }),
    );
    expect(screen.getByTestId('tour-status')).toHaveTextContent('inactive');
    fireEvent.click(screen.getByTestId('routes-start-tour'));
    expect(screen.getByTestId('tour-status')).toHaveTextContent('active:0');
  });

  it('wraps the app root in ErrorBoundary to catch descendant render failures', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    shouldThrowInRoutes = true;
    render(<App />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: /something went wrong/i })).toBeInTheDocument();
  });
});
