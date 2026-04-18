import { DashboardView } from './components/DashboardView';
import { useDashboardLogic } from './useDashboardLogic';

export function App() {
  const logic = useDashboardLogic();
  return <DashboardView {...logic} />;
}
