import { useMemo, useState } from 'react';
import { apiGet, formatUserError } from '../api';
import { buildDrugAliasLookup, downloadTsv } from '../utils';

const SHARED_COLUMN_LABELS = {
  reference_name: 'Reference',
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

const MUTATION_COLUMN_LABELS = {
  ...SHARED_COLUMN_LABELS,
  feature: 'Sequence Feature',
  position: 'Pos',
  reference: 'Reference AA',
  mutation: 'Mutation',
};

const FORMULA_COLUMN_LABELS = {
  ...SHARED_COLUMN_LABELS,
  formula_id: 'Formula ID',
  label: 'Label',
  normalized_expression: 'Expression',
  member_count: 'Members',
};

const MUTATION_COLUMN_ORDER = [
  'reference_name', 'feature', 'position', 'reference', 'mutation',
  'drug', 'drug_group', 'phenotype', 'clinical_phenotype',
  'ic50', 'fold_ic50', 'score', 'source', 'publication', 'comment',
];

const FORMULA_COLUMN_ORDER = [
  'reference_name', 'drug', 'drug_group', 'formula_id', 'label',
  'normalized_expression', 'member_count', 'phenotype', 'clinical_phenotype',
  'ic50', 'fold_ic50', 'score', 'source', 'publication', 'comment',
];

function buildRuleColumns(columnKeys, { labels, order, formatPosition = false, plotMeta = {} }) {
  const drugAliasLookup = buildDrugAliasLookup(plotMeta);
  const sortIndex = (key) => {
    const idx = order.indexOf(key);
    return idx === -1 ? order.length + 100 : idx;
  };

  return [...columnKeys]
    .sort((a, b) => sortIndex(a) - sortIndex(b))
    .map((key) => {
      const accessor = (rule) => {
        if (formatPosition && key === 'position') {
          const pos = Number(rule.position);
          if (Number.isFinite(pos)) return String(pos + 1);
        }
        if (key === 'drug') {
          const drugName = String(rule.drug || '').trim();
          if (!drugName) return '';
          const alias = drugAliasLookup.get(drugName.toLowerCase()) || '';
          return alias ? `${drugName} (${alias})` : drugName;
        }
        const value = rule[key];
        return value === null || value === undefined ? '' : String(value);
      };
      return {
        key,
        label: labels[key] || key,
        accessor,
        sortAccessor: key === 'drug' ? (rule) => String(rule.drug || '') : accessor,
      };
    });
}

function parseSortableValue(text) {
  const raw = (text || '').trim();
  const num = Number(raw);
  return !Number.isNaN(num) && raw !== ''
    ? { kind: 'num', value: num }
    : { kind: 'txt', value: raw.toLowerCase() };
}

function filterRows(rows, columns, query) {
  if (!query) return rows;
  const lowered = query.toLowerCase();
  return rows.filter((row) =>
    columns.map((column) => column.accessor(row)).join(' ').toLowerCase().includes(lowered),
  );
}

export function useMutationBrowser({ setStatusError }) {
  const [mutationFilter, setMutationFilter] = useState('');
  const [mutationSortColumn, setMutationSortColumn] = useState(null);
  const [mutationSortAsc, setMutationSortAsc] = useState(true);
  const [formulaFilter, setFormulaFilter] = useState('');
  const [mutationPlotMeta, setMutationPlotMeta] = useState({ references: [], features: [] });
  const [mutationsLoaded, setMutationsLoaded] = useState(false);
  const [rules, setRules] = useState([]);
  const [formulaRules, setFormulaRules] = useState([]);

  const mutationColumns = useMemo(
    () => buildRuleColumns(rules[0] ? Object.keys(rules[0]) : [], {
      labels: MUTATION_COLUMN_LABELS,
      order: MUTATION_COLUMN_ORDER,
      formatPosition: true,
      plotMeta: mutationPlotMeta,
    }),
    [rules, mutationPlotMeta],
  );

  const formulaColumns = useMemo(
    () => buildRuleColumns(formulaRules[0] ? Object.keys(formulaRules[0]) : [], {
      labels: FORMULA_COLUMN_LABELS,
      order: FORMULA_COLUMN_ORDER,
      plotMeta: mutationPlotMeta,
    }),
    [formulaRules, mutationPlotMeta],
  );

  const displayedRules = useMemo(() => {
    const filtered = filterRows(rules, mutationColumns, mutationFilter);
    if (mutationSortColumn === null) return filtered;
    const selectedColumn = mutationColumns[mutationSortColumn];
    const getColValue = selectedColumn ? (selectedColumn.sortAccessor || selectedColumn.accessor) : null;
    if (!getColValue) return filtered;

    return [...filtered].sort((a, b) => {
      const av = parseSortableValue(String(getColValue(a)));
      const bv = parseSortableValue(String(getColValue(b)));
      const cmp = av.kind === 'num' && bv.kind === 'num'
        ? av.value - bv.value
        : av.value < bv.value ? -1 : av.value > bv.value ? 1 : 0;
      return mutationSortAsc ? cmp : -cmp;
    });
  }, [rules, mutationColumns, mutationFilter, mutationSortColumn, mutationSortAsc]);

  const displayedFormulaRules = useMemo(
    () => filterRows(formulaRules, formulaColumns, formulaFilter),
    [formulaRules, formulaColumns, formulaFilter],
  );

  const loadMutations = async (databaseId) => {
    try {
      const payload = await apiGet('/api/mutations', { database_id: databaseId });
      setRules(payload.data.items || []);
      setFormulaRules(payload.data.formula_items || []);
      setMutationPlotMeta(payload.data.plot_meta || { references: [], features: [] });
      setMutationsLoaded(true);
    } catch (error) {
      setStatusError(`Error loading mutations: ${error.message}`);
    }
  };

  const exportRulesTsv = (columns, rows, filename) => {
    downloadTsv(
      columns.map((column) => column.label),
      rows.map((row) => columns.map((column) => column.accessor(row))),
      filename,
    );
  };

  return {
    mutationFilter,
    setMutationFilter,
    mutationSortColumn,
    setMutationSortColumn,
    mutationSortAsc,
    setMutationSortAsc,
    formulaFilter,
    setFormulaFilter,
    mutationPlotMeta,
    mutationsLoaded,
    rules,
    formulaRules,
    mutationColumns,
    formulaColumns,
    displayedRules,
    displayedFormulaRules,
    loadMutations,
    downloadMutationsAsTsv: () => exportRulesTsv(mutationColumns, displayedRules, 'single-mutations.tsv'),
    downloadFormulaRulesAsTsv: () => exportRulesTsv(formulaColumns, displayedFormulaRules, 'formula-combinations.tsv'),
  };
}
