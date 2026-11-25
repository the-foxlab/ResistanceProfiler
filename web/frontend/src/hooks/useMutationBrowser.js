import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiGet, formatUserError, isAbortError } from '../api';
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

export function useDatabaseRules({ setStatusError, onDatabaseSwitch } = {}) {
  const [mutationPlotMeta, setMutationPlotMeta] = useState({ references: [], features: [] });
  const [mutationsLoaded, setMutationsLoaded] = useState(false);
  const [isMutationsLoading, setIsMutationsLoading] = useState(false);
  const [loadedDatabaseId, setLoadedDatabaseId] = useState(null);
  const [rules, setRules] = useState([]);
  const [formulaRules, setFormulaRules] = useState([]);

  const isMountedRef = useRef(true);
  const requestIdRef = useRef(0);
  const abortControllerRef = useRef(null);
  const activeDatabaseIdRef = useRef(null);
  const lastRequestedDatabaseIdRef = useRef(null);
  const hadMutationErrorRef = useRef(false);
  const setStatusErrorRef = useRef(setStatusError);
  setStatusErrorRef.current = setStatusError;
  const onDatabaseSwitchRef = useRef(onDatabaseSwitch);
  onDatabaseSwitchRef.current = onDatabaseSwitch;

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      requestIdRef.current += 1;
      activeDatabaseIdRef.current = null;
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
    };
  }, []);

  const loadMutations = useCallback(async (databaseId, { signal: externalSignal } = {}) => {
    const normalizedDbId = databaseId || '';
    if (!normalizedDbId && lastRequestedDatabaseIdRef.current === null && !abortControllerRef.current) {
      return;
    }

    const requestId = ++requestIdRef.current;
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }

    setMutationsLoaded(false);
    setLoadedDatabaseId(null);
    setRules([]);
    setFormulaRules([]);
    setMutationPlotMeta({ references: [], features: [] });

    if (normalizedDbId !== lastRequestedDatabaseIdRef.current) {
      onDatabaseSwitchRef.current?.();
      lastRequestedDatabaseIdRef.current = normalizedDbId || null;
    }

    if (hadMutationErrorRef.current) {
      hadMutationErrorRef.current = false;
      setStatusErrorRef.current?.('');
    }

    if (!normalizedDbId) {
      activeDatabaseIdRef.current = null;
      setIsMutationsLoading(false);
      return;
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;
    activeDatabaseIdRef.current = normalizedDbId;
    setIsMutationsLoading(true);

    let detachExternalAbort = null;
    if (externalSignal) {
      if (externalSignal.aborted) {
        controller.abort();
      } else if (typeof externalSignal.addEventListener === 'function') {
        const onExternalAbort = () => controller.abort();
        externalSignal.addEventListener('abort', onExternalAbort, { once: true });
        detachExternalAbort = () => {
          externalSignal.removeEventListener?.('abort', onExternalAbort);
        };
      }
    }

    try {
      if (controller.signal.aborted) {
        return;
      }
      const payload = await apiGet('/api/mutations', { database_id: normalizedDbId }, { signal: controller.signal });
      if (
        !isMountedRef.current
        || requestId !== requestIdRef.current
        || controller.signal.aborted
        || activeDatabaseIdRef.current !== normalizedDbId
      ) {
        return;
      }
      const data = payload?.data || {};
      setRules(data.items || []);
      setFormulaRules(data.formula_items || []);
      setMutationPlotMeta(data.plot_meta || { references: [], features: [] });
      setLoadedDatabaseId(normalizedDbId);
      setMutationsLoaded(true);
    } catch (error) {
      if (
        !isMountedRef.current
        || requestId !== requestIdRef.current
        || controller.signal.aborted
        || isAbortError(error)
      ) {
        return;
      }
      setRules([]);
      setFormulaRules([]);
      setMutationPlotMeta({ references: [], features: [] });
      setLoadedDatabaseId(null);
      setMutationsLoaded(false);
      hadMutationErrorRef.current = true;
      setStatusErrorRef.current?.(`Error loading mutations: ${formatUserError(error?.message)}`);
    } finally {
      detachExternalAbort?.();
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
      }
      if (isMountedRef.current && requestId === requestIdRef.current) {
        setIsMutationsLoading(false);
      }
    }
  }, []);

  return {
    mutationPlotMeta,
    mutationsLoaded,
    isMutationsLoading,
    loadedDatabaseId,
    rules,
    formulaRules,
    loadMutations,
  };
}

export function useMutationTableFilter({ rules, formulaRules, mutationPlotMeta, mutationsLoaded }) {
  const [mutationFilter, setMutationFilter] = useState('');
  const [mutationSortColumn, setMutationSortColumn] = useState(null);
  const [mutationSortAsc, setMutationSortAsc] = useState(true);
  const [formulaFilter, setFormulaFilter] = useState('');

  const resetFilters = useCallback(() => {
    setMutationFilter('');
    setFormulaFilter('');
    setMutationSortColumn(null);
    setMutationSortAsc(true);
  }, []);

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

  const downloadMutationsAsTsv = useCallback(() => {
    if (!mutationsLoaded) return;
    downloadTsv(
      mutationColumns.map((column) => column.label),
      displayedRules.map((row) => mutationColumns.map((column) => column.accessor(row))),
      'single-mutations.tsv',
    );
  }, [mutationsLoaded, mutationColumns, displayedRules]);

  const downloadFormulaRulesAsTsv = useCallback(() => {
    if (!mutationsLoaded) return;
    downloadTsv(
      formulaColumns.map((column) => column.label),
      displayedFormulaRules.map((row) => formulaColumns.map((column) => column.accessor(row))),
      'formula-combinations.tsv',
    );
  }, [mutationsLoaded, formulaColumns, displayedFormulaRules]);

  return {
    mutationFilter,
    setMutationFilter,
    mutationSortColumn,
    setMutationSortColumn,
    mutationSortAsc,
    setMutationSortAsc,
    formulaFilter,
    setFormulaFilter,
    mutationColumns,
    formulaColumns,
    displayedRules,
    displayedFormulaRules,
    resetFilters,
    downloadMutationsAsTsv,
    downloadFormulaRulesAsTsv,
  };
}

export function useMutationBrowser({ setStatusError }) {
  const resetFiltersRef = useRef(null);
  const handleDatabaseSwitch = useCallback(() => {
    resetFiltersRef.current?.();
  }, []);

  const dbRules = useDatabaseRules({
    setStatusError,
    onDatabaseSwitch: handleDatabaseSwitch,
  });
  const tableFilter = useMutationTableFilter(dbRules);
  resetFiltersRef.current = tableFilter.resetFilters;

  return {
    mutationFilter: tableFilter.mutationFilter,
    setMutationFilter: tableFilter.setMutationFilter,
    mutationSortColumn: tableFilter.mutationSortColumn,
    setMutationSortColumn: tableFilter.setMutationSortColumn,
    mutationSortAsc: tableFilter.mutationSortAsc,
    setMutationSortAsc: tableFilter.setMutationSortAsc,
    formulaFilter: tableFilter.formulaFilter,
    setFormulaFilter: tableFilter.setFormulaFilter,
    mutationPlotMeta: dbRules.mutationPlotMeta,
    mutationsLoaded: dbRules.mutationsLoaded,
    isMutationsLoading: dbRules.isMutationsLoading,
    loadedDatabaseId: dbRules.loadedDatabaseId,
    rules: dbRules.rules,
    formulaRules: dbRules.formulaRules,
    mutationColumns: tableFilter.mutationColumns,
    formulaColumns: tableFilter.formulaColumns,
    displayedRules: tableFilter.displayedRules,
    displayedFormulaRules: tableFilter.displayedFormulaRules,
    loadMutations: dbRules.loadMutations,
    downloadMutationsAsTsv: tableFilter.downloadMutationsAsTsv,
    downloadFormulaRulesAsTsv: tableFilter.downloadFormulaRulesAsTsv,
  };
}
