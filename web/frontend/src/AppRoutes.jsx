import { Routes, Route, useLocation } from 'react-router';

import { usePageTitle } from './hooks/usePageTitle';
import { resolveRouteMeta } from './routes';
import { HomePage } from './components/pages/HomePage';
import { AnalyzePage } from './components/pages/AnalyzePage';
import { ResultsPage } from './components/pages/ResultsPage';
import { DatabasePage } from './components/pages/DatabasePage';
import { MutationsPage } from './components/pages/MutationsPage';
import { ComparePage } from './components/pages/ComparePage';
import { AboutPage } from './components/pages/AboutPage';
import { NotFoundPage } from './components/pages/NotFoundPage';

export function AppRoutes({ logic }) {
  const { pathname } = useLocation();
  const meta = resolveRouteMeta(pathname);
  usePageTitle(meta.title);

  return (
    <Routes>
      <Route path="/" element={<HomePage logic={logic} />} />
      <Route path="/analysis" element={<AnalyzePage logic={logic} />} />
      <Route path="/analysis/reports" element={<ResultsPage logic={logic} />} />
      <Route path="/databases" element={<DatabasePage logic={logic} />} />
      <Route path="/databases/mutations" element={<MutationsPage logic={logic} />} />
      <Route path="/databases/compare" element={<ComparePage logic={logic} />} />
      <Route path="/about" element={<AboutPage logic={logic} />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
