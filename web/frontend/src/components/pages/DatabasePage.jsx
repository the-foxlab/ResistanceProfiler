import { DatabaseTab } from '../tabs/DatabaseTab';

export function DatabasePage({ logic }) {
  return (
    <div className="page-workspace">
      <DatabaseTab
        rules={logic.rules}
        formulaRules={logic.formulaRules}
        mutationPlotMeta={logic.mutationPlotMeta}
        selectedDatabase={logic.selectedDatabase}
      />
    </div>
  );
}
