import { useMemo, useState } from 'react';
import { apiGet, formatUserError } from '../api';
import { buildDrugAliasLookup } from '../utils';

const MUTATION_COLUMN_LABELS = {
  reference_name: 'Reference',
  feature: 'Sequence Feature',
  position: 'Pos',
  reference: 'Reference AA',
  mutation: 'Mutation',
  drug: 'Drug',
  drug_group: 'Drug Group',
  phenotype: 'Phenotype',
  clinical_phenotype: 'Clinical phenotype',
  ic50: 'IC50',
  fold_ic50: 'Fold IC50',
  publication: 'DOI',
  source: 'Source',
  comment: 'Comment',
};

const MUTATION_COLUMN_ORDER = [
  'reference_name',
  'feature',
  'position',
  'reference',
  'mutation',
  'drug',
  'drug_group',
  'phenotype',
  'clinical_phenotype',
  'ic50',
  'fold_ic50',
  'score',
  'source',
  'publication',
  'comment',
];

const FORMULA_COLUMN_LABELS = {
  reference_name: 'Reference',
  drug: 'Drug',
  drug_group: 'Drug Group',
  formula_id: 'Formula ID',
  label: 'Label',
  normalized_expression: 'Expression',
  member_count: 'Members',
  phenotype: 'Phenotype',
  clinical_phenotype: 'Clinical phenotype',
  ic50: 'IC50',
  fold_ic50: 'Fold IC50',
  publication: 'DOI',
  source: 'Source',
  comment: 'Comment',
};

const FORMULA_COLUMN_ORDER = [
  'reference_name',
  'drug',
  'drug_group',
  'formula_id',
  'label',
  'normalized_expression',
  'member_count',
  'phenotype',
  'clinical_phenotype',
  'ic50',
  'fold_ic50',
  'score',
  'source',
  'publication',
  'comment',
];

function _mutationColumnSortIndex(columnKey) {
  // Unknown columns are still shown, but pushed behind the known stable order.
  const idx = MUTATION_COLUMN_ORDER.indexOf(columnKey);
  return idx === -1 ? MUTATION_COLUMN_ORDER.length + 100 : idx;
}

export function buildMutationColumns(columnKeys, plotMeta = {}) {
  const drugAliasLookup = buildDrugAliasLookup(plotMeta);
  return [...columnKeys]
    .sort((a, b) => _mutationColumnSortIndex(a) - _mutationColumnSortIndex(b))
    .map((columnKey) => {
      const label = MUTATION_COLUMN_LABELS[columnKey] || columnKey;
      const accessor = (rule) => {
        // Rule positions are stored 0-based in the backend and displayed 1-based in the UI.
        if (columnKey === 'position') {
          const positionValue = Number(rule.position);
          if (Number.isFinite(positionValue)) {
            return String(positionValue + 1);
          }
        }
        if (columnKey === 'drug') {
          const drugName = String(rule.drug || '').trim();
          if (!drugName) {
            return '';
          }
          const alias = _resolveDrugAlias(drugName, drugAliasLookup);
          return alias ? `${drugName} (${alias})` : drugName;
        }
        const value = rule[columnKey];
        if (value === null || value === undefined) {
          return '';
        }
        return String(value);
      };
      return {
        key: columnKey,
        label,
        accessor,
        sortAccessor: columnKey === 'drug' ? (rule) => String(rule.drug || '') : accessor,
      };
    });
}

function _formulaColumnSortIndex(columnKey) {
  const idx = FORMULA_COLUMN_ORDER.indexOf(columnKey);
  return idx === -1 ? FORMULA_COLUMN_ORDER.length + 100 : idx;
}

export function buildFormulaColumns(columnKeys, plotMeta = {}) {
  const drugAliasLookup = buildDrugAliasLookup(plotMeta);
  return [...columnKeys]
    .sort((a, b) => _formulaColumnSortIndex(a) - _formulaColumnSortIndex(b))
    .map((columnKey) => {
      const label = FORMULA_COLUMN_LABELS[columnKey] || columnKey;
      const accessor = (formulaRule) => {
        if (columnKey === 'drug') {
          const drugName = String(formulaRule.drug || '').trim();
          if (!drugName) {
            return '';
          }
          const alias = _resolveDrugAlias(drugName, drugAliasLookup);
          return alias ? `${drugName} (${alias})` : drugName;
        }
        const value = formulaRule[columnKey];
        if (value === null || value === undefined) {
          return '';
        }
        return String(value);
      };
      return {
        key: columnKey,
        label,
        accessor,
        sortAccessor: columnKey === 'drug' ? (formulaRule) => String(formulaRule.drug || '') : accessor,
      };
    });
}

function _resolveDrugAlias(drugName, aliasLookup) {
  return aliasLookup.get(String(drugName || '').trim().toLowerCase()) || '';
}

export function useMutationBrowser({ selectedDatabaseId, setStatusError }) {
  const [mutationFilter, setMutationFilter] = useState('');
  const [mutationFilterColumn, setMutationFilterColumn] = useState('-1');
  const [mutationSortColumn, setMutationSortColumn] = useState(null);
  const [mutationSortAsc, setMutationSortAsc] = useState(true);
  const [formulaFilter, setFormulaFilter] = useState('');
  const [formulaFilterColumn, setFormulaFilterColumn] = useState('-1');
  const [mutationColumnKeys, setMutationColumnKeys] = useState([]);
  const [formulaColumnKeys, setFormulaColumnKeys] = useState([]);
  const [mutationPlotMeta, setMutationPlotMeta] = useState({ references: [], features: [] });
  const [mutationsLoaded, setMutationsLoaded] = useState(false);
  const [rules, setRules] = useState([]);
  const [formulaRules, setFormulaRules] = useState([]);

  const mutationColumns = useMemo(() => {
    return buildMutationColumns(
      mutationColumnKeys.length > 0
        ? mutationColumnKeys
        : (rules[0] ? Object.keys(rules[0]) : []),
      mutationPlotMeta
    );
  }, [mutationColumnKeys, rules, mutationPlotMeta]);

  const formulaColumns = useMemo(() => {
    return buildFormulaColumns(
      formulaColumnKeys.length > 0
        ? formulaColumnKeys
        : (formulaRules[0] ? Object.keys(formulaRules[0]) : []),
      mutationPlotMeta
    );
  }, [formulaColumnKeys, formulaRules, mutationPlotMeta]);

  const parseValue = (text) => {
    // Sorting prefers numeric comparison when possible, otherwise case-insensitive text.
    const raw = (text || '').trim();
    const num = Number(raw);
    if (!Number.isNaN(num) && raw !== '') {
      return { kind: 'num', value: num };
    }
    return { kind: 'txt', value: raw.toLowerCase() };
  };

  const filterMutations = (rulesList) => {
    // Column filter supports either one selected column or "search in all columns".
    if (!mutationFilter) {
      return rulesList;
    }
    const query = mutationFilter.toLowerCase();
    const colIdx = Number(mutationFilterColumn);

    return rulesList.filter((rule) => {
      if (colIdx === -1) {
        const haystack = mutationColumns.map((column) => column.accessor(rule))
          .join(' ')
          .toLowerCase();
        return haystack.includes(query);
      }
      const selectedColumn = mutationColumns[colIdx];
      const cellText = selectedColumn ? selectedColumn.accessor(rule) : '';
      return cellText.toLowerCase().includes(query);
    });
  };

  const sortMutations = (rulesToSort) => {
    if (mutationSortColumn === null) {
      return rulesToSort;
    }

    const selectedColumn = mutationColumns[mutationSortColumn];
    const getColValue = selectedColumn ? (selectedColumn.sortAccessor || selectedColumn.accessor) : null;
    if (!getColValue) {
      return rulesToSort;
    }

    return [...rulesToSort].sort((a, b) => {
      const av = parseValue(String(getColValue(a)));
      const bv = parseValue(String(getColValue(b)));

      let cmp = 0;
      if (av.kind === 'num' && bv.kind === 'num') {
        cmp = av.value - bv.value;
      } else if (av.value < bv.value) {
        cmp = -1;
      } else if (av.value > bv.value) {
        cmp = 1;
      }
      return mutationSortAsc ? cmp : -cmp;
    });
  };

  const displayedRules = sortMutations(filterMutations(rules));

  const filterFormulaRules = (formulaRuleList) => {
    if (!formulaFilter) {
      return formulaRuleList;
    }

    const query = formulaFilter.toLowerCase();
    const colIdx = Number(formulaFilterColumn);

    return formulaRuleList.filter((formulaRule) => {
      if (colIdx === -1) {
        const haystack = formulaColumns.map((column) => column.accessor(formulaRule))
          .join(' ')
          .toLowerCase();
        return haystack.includes(query);
      }

      const selectedColumn = formulaColumns[colIdx];
      const cellText = selectedColumn ? selectedColumn.accessor(formulaRule) : '';
      return cellText.toLowerCase().includes(query);
    });
  };

  const displayedFormulaRules = filterFormulaRules(formulaRules);

  const loadMutations = async (databaseId) => {
    // Mutation browser + database charts both read from this payload.
    try {
      const payload = await apiGet('/api/mutations', { database_id: databaseId });
      const items = payload.data.items || [];
      const columns = payload.data.columns || (items.length > 0 ? Object.keys(items[0]) : []);
      const formulaItems = payload.data.formula_items || [];
      const formulaColumns = payload.data.formula_columns
        || (formulaItems.length > 0 ? Object.keys(formulaItems[0]) : []);
      const plotMeta = payload.data.plot_meta || { references: [], features: [] };
      setRules(items);
      setFormulaRules(formulaItems);
      setMutationColumnKeys(columns);
      setFormulaColumnKeys(formulaColumns);
      setMutationPlotMeta(plotMeta);
      setMutationsLoaded(true);
      // Keep status area quiet after background mutation loading to reduce UI noise.
    } catch (error) {
      setStatusError(`Error loading mutations: ${error.message}`);
    }
  };

  const downloadMutationsAsTsv = () => {
    // Export exactly what is currently visible (after filter/sort), not raw backend order.
    const headers = mutationColumns.map((column) => column.label);
    const lines = [headers.join('\t')];
    displayedRules.forEach((rule) => {
      const row = mutationColumns.map((column) => {
        const raw = column.accessor(rule);
        return String(raw ?? '').replace(/\t/g, ' ').replace(/\r?\n/g, ' ');
      });
      lines.push(row.join('\t'));
    });

    const blob = new Blob([`${lines.join('\n')}\n`], { type: 'text/tab-separated-values;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = 'single-mutations.tsv';
    anchor.click();
    URL.revokeObjectURL(href);
  };

  const downloadFormulaRulesAsTsv = () => {
    const headers = formulaColumns.map((column) => column.label);
    const lines = [headers.join('\t')];
    displayedFormulaRules.forEach((formulaRule) => {
      const row = formulaColumns.map((column) => {
        const raw = column.accessor(formulaRule);
        return String(raw ?? '').replace(/\t/g, ' ').replace(/\r?\n/g, ' ');
      });
      lines.push(row.join('\t'));
    });

    const blob = new Blob([`${lines.join('\n')}\n`], { type: 'text/tab-separated-values;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = 'formula-combinations.tsv';
    anchor.click();
    URL.revokeObjectURL(href);
  };

  return {
    mutationFilter,
    setMutationFilter,
    mutationFilterColumn,
    setMutationFilterColumn,
    mutationSortColumn,
    setMutationSortColumn,
    mutationSortAsc,
    setMutationSortAsc,
    formulaFilter,
    setFormulaFilter,
    formulaFilterColumn,
    setFormulaFilterColumn,
    mutationColumnKeys,
    mutationPlotMeta,
    mutationsLoaded,
    rules,
    setRules,
    formulaRules,
    setFormulaRules,
    mutationColumns,
    formulaColumns,
    displayedRules,
    displayedFormulaRules,
    loadMutations,
    downloadMutationsAsTsv,
    downloadFormulaRulesAsTsv,
    setMutationColumnKeys,
    setFormulaColumnKeys,
    setMutationPlotMeta,
    setMutationsLoaded,
  };
}
