import React, { useMemo } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';

import { AppRoutes } from './AppRoutes';
import { AppShell } from './components/AppShell';
import { ErrorBoundary } from './components/ErrorBoundary';
import { TourProvider } from './components/tour/TourContext';
import { buildTourSteps } from './components/tour/steps';
import { DashboardProvider, useCoreActions } from './DashboardContext';
import './styles/index.css';

// Everything below lives INSIDE <BrowserRouter> because DashboardProvider uses router hooks.
function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <AppInsideRouter />
      </BrowserRouter>
    </ErrorBoundary>
  );
}

function AppInsideRouter() {
  return (
    <DashboardProvider>
      <AppTourShell />
    </DashboardProvider>
  );
}

function AppTourShell() {
  const { navigate, setActiveProfileMode, setAnalyzeSubMode } = useCoreActions();
  const tourSteps = useMemo(
    () => buildTourSteps({
      navigate,
      setActiveProfileMode,
      setAnalyzeSubMode,
    }),
    [navigate, setActiveProfileMode, setAnalyzeSubMode],
  );
  return (
    <TourProvider steps={tourSteps}>
      <AppShell>
        <AppRoutes />
      </AppShell>
    </TourProvider>
  );
}

export { App };

const rootEl = document.getElementById('root');
if (rootEl) {
  createRoot(rootEl).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}
