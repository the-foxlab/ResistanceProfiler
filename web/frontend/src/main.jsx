import React, { useMemo } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';

import { AppRoutes } from './AppRoutes';
import { AppShell } from './components/AppShell';
import { TourProvider, useTour } from './components/tour/TourContext';
import { buildTourSteps } from './components/tour/steps';
import { useDashboardLogic } from './useDashboardLogic';
import './styles/index.css';

// Everything below lives INSIDE <BrowserRouter> because useDashboardLogic() uses router hooks.
function App() {
  return (
    <BrowserRouter>
      <AppInsideRouter />
    </BrowserRouter>
  );
}

function AppInsideRouter() {
  const logic = useDashboardLogic();
  const tourSteps = useMemo(
    () => buildTourSteps({
      navigate: logic.navigate,
      setActiveProfileMode: logic.setActiveProfileMode,
      setAnalyzeSubMode: logic.setAnalyzeSubMode,
    }),
    [logic.navigate, logic.setActiveProfileMode, logic.setAnalyzeSubMode],
  );
  return (
    <TourProvider steps={tourSteps}>
      <AppWithTour logic={logic} />
    </TourProvider>
  );
}

function AppWithTour({ logic }) {
  const { startTour } = useTour();
  return (
    <AppShell logic={logic}>
      <AppRoutes logic={{ ...logic, onStartTour: startTour }} />
    </AppShell>
  );
}

export { App };

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
