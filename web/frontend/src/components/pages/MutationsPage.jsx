import { MutationsTab } from '../tabs/MutationsTab';

export function MutationsPage({ logic }) {
  return (
    <div className="page-workspace">
      <MutationsTab
        rules={logic.rules}
        formulaRules={logic.formulaRules}
        mutationColumns={logic.mutationColumns}
        formulaColumns={logic.formulaColumns}
        displayedRules={logic.displayedRules}
        displayedFormulaRules={logic.displayedFormulaRules}
        mutationFilter={logic.mutationFilter}
        setMutationFilter={logic.setMutationFilter}
        mutationFilterColumn={logic.mutationFilterColumn}
        setMutationFilterColumn={logic.setMutationFilterColumn}
        mutationSortColumn={logic.mutationSortColumn}
        setMutationSortColumn={logic.setMutationSortColumn}
        mutationSortAsc={logic.mutationSortAsc}
        setMutationSortAsc={logic.setMutationSortAsc}
        formulaFilter={logic.formulaFilter}
        setFormulaFilter={logic.setFormulaFilter}
        formulaFilterColumn={logic.formulaFilterColumn}
        setFormulaFilterColumn={logic.setFormulaFilterColumn}
        mutationPlotMeta={logic.mutationPlotMeta}
        mutationsLoaded={logic.mutationsLoaded}
        downloadMutationsAsTsv={logic.downloadMutationsAsTsv}
        downloadFormulaRulesAsTsv={logic.downloadFormulaRulesAsTsv}
        databases={logic.databases}
        selectedDatabaseId={logic.selectedDatabaseId}
      />
    </div>
  );
}
