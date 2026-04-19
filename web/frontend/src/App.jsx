import { DashboardView } from './components/DashboardView';
import { useDashboardLogic } from './useDashboardLogic';

export function App() {
  // Keep data/state logic in one hook and rendering in a dedicated view component.
  const logic = useDashboardLogic();
  return <DashboardView {...logic} />;
}
