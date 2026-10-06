import { CompareTab } from '../tabs/CompareTab';

export function ComparePage({ logic }) {
  return (
    <div className="page-workspace">
      <CompareTab databases={logic.databases} />
    </div>
  );
}
