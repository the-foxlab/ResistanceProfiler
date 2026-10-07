import React, { useMemo } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';

import { AppRoutes } from './AppRoutes';
import { AppShell } from './components/AppShell';
import { TourProvider, useTour } from './components/tour/TourContext';
import { buildTourSteps } from './components/tour/steps';
import { useDashboardLogic } from './useDashboardLogic';
import { useMobileClass } from './hooks/useMobileClass';
import './fonts.css';
import './styles.css';

// Everything below lives INSIDE <BrowserRouter>: useDashboardLogic() calls
// useLocation()/useNavigate(), which throw outside a Router context. Mounting
// them in the component that renders the Router crashed the production bundle
// before first paint (blank page), while jsdom tests that wrap components in
// their own MemoryRouter never saw the failure.
function App() {
  return (
    <BrowserRouter>
      <AppInsideRouter />
    </BrowserRouter>
  );
}

function AppInsideRouter() {
  // Data/state logic lives in one hook mounted above the routes so session
  // results, uploads and comparison state survive in-app navigation.
  const logic = useDashboardLogic();
  // Reflect mobile layout mode on <body> for any JS that needs it.
  useMobileClass();
  // Build the tour steps once with the real navigation setters (stable useState setters)
  // and pass them to the provider so its nextStep/prevStep clamping uses the real step
  // count. The overlay reads the same steps from context. (Fixes the Critical wiring bug
  // where steps={[]} made nextStep clamp to -1 and the tour died after the first Next.)
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

// Lives inside TourProvider so Home can start the guided tour.
function AppWithTour({ logic }) {
  const { startTour } = useTour();
  return (
    <AppShell logic={logic}>
      <AppRoutes logic={{ ...logic, onStartTour: startTour }} />
    </AppShell>
  );
}

// Named export so tests can mount <App /> bare (no wrapper Router) and catch
// router-context wiring bugs that only appear in the production bundle.
export { App };

// StrictMode helps surface side effects and unsafe patterns during development.
createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
