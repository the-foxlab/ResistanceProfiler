import { Suspense, lazy, memo } from 'react';
import { Routes, Route, useLocation } from 'react-router';

import { Spinner } from './components/Spinner';
import { usePageTitle } from './hooks/usePageTitle';
import { resolveRouteMeta } from './routes';

const HomePage = lazy(() => import('./components/pages/HomePage').then((m) => ({ default: m.HomePage })));
const AnalyzePage = lazy(() => import('./components/pages/AnalyzePage').then((m) => ({ default: m.AnalyzePage })));
const ResultsPage = lazy(() => import('./components/pages/ResultsPage').then((m) => ({ default: m.ResultsPage })));
const DatabasePage = lazy(() => import('./components/pages/DatabasePage').then((m) => ({ default: m.DatabasePage })));
const MutationsPage = lazy(() => import('./components/pages/MutationsPage').then((m) => ({ default: m.MutationsPage })));
const ComparePage = lazy(() => import('./components/pages/ComparePage').then((m) => ({ default: m.ComparePage })));
const AboutPage = lazy(() => import('./components/pages/AboutPage').then((m) => ({ default: m.AboutPage })));
const NotFoundPage = lazy(() => import('./components/pages/NotFoundPage').then((m) => ({ default: m.NotFoundPage })));

function RouteLoadingFallback() {
  return (
    <p className="status" role="status" aria-live="polite">
      <Spinner /> Loading page…
    </p>
  );
}

export const AppRoutes = memo(function AppRoutes({ logic }) {
  const { pathname } = useLocation();
  const meta = resolveRouteMeta(pathname);
  usePageTitle(meta.title);

  const routeProps = logic !== undefined ? { logic } : {};

  return (
    <Suspense fallback={<RouteLoadingFallback />}>
      <Routes>
        <Route path="/" element={<HomePage {...routeProps} />} />
        <Route path="/analysis" element={<AnalyzePage {...routeProps} />} />
        <Route path="/analysis/reports" element={<ResultsPage {...routeProps} />} />
        <Route path="/databases" element={<DatabasePage {...routeProps} />} />
        <Route path="/databases/mutations" element={<MutationsPage {...routeProps} />} />
        <Route path="/databases/compare" element={<ComparePage {...routeProps} />} />
        <Route path="/about" element={<AboutPage {...routeProps} />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </Suspense>
  );
});
