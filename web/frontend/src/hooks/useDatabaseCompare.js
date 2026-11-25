import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  fetchSharedReferences,
  fetchReferenceAccessions,
  compareDatabases,
  isAbortError,
} from '../api';
import { downloadTsv } from '../utils';
import { regionKey } from '../components/vennAreas';
import { getCompareState, setCompareState } from '../components/pages/compareSessionState';
import { MIN_COMPARE_DATABASES } from '../components/pages/compare/CompareSelectorsCard';

const METADATA_FIELDS = [
  { key: 'drug', label: 'Drug' },
  { key: 'phenotype', label: 'Phenotype' },
  { key: 'ic50', label: 'IC50' },
  { key: 'fold_ic50', label: 'Fold-IC50' },
  { key: 'score', label: 'Score' },
  { key: 'publication', label: 'Publication' },
];

/**
 * Manages state, session persistence, progressive slot filtering, API calls,
 * and TSV export for the Compare Databases page.
 */
export function useDatabaseCompare(databases) {
  const persisted = getCompareState();
  const [slots, setSlots] = useState(persisted.slots);
  const [accessionsByDb, setAccessionsByDb] = useState(null);
  const [sharedRefs, setSharedRefs] = useState(persisted.sharedRefs);
  const [sharedRefsLoading, setSharedRefsLoading] = useState(false);
  const [accession, setAccessionState] = useState(persisted.accession);
  const [response, setResponse] = useState(persisted.response);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState('');
  const [regionFilter, setRegionFilter] = useState(persisted.regionFilter);
  const [expandedKey, setExpandedKey] = useState(persisted.expandedKey);
  const [tableSearch, setTableSearch] = useState(persisted.tableSearch);
  const loadedRefsKeyRef = useRef(persisted.sharedRefsKey);
  const isMountedRef = useRef(true);
  const accessionsRequestIdRef = useRef(0);
  const accessionsAbortRef = useRef(null);
  const sharedRefsRequestIdRef = useRef(0);
  const sharedRefsAbortRef = useRef(null);
  const compareRequestIdRef = useRef(0);
  const compareAbortRef = useRef(null);
  const currentCompareKeyRef = useRef('');

  const selectedIds = useMemo(() => slots.filter(Boolean), [slots]);
  currentCompareKeyRef.current = `${selectedIds.join(',')}::${accession}`;

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      accessionsRequestIdRef.current += 1;
      sharedRefsRequestIdRef.current += 1;
      compareRequestIdRef.current += 1;
      if (accessionsAbortRef.current) {
        accessionsAbortRef.current.abort();
        accessionsAbortRef.current = null;
      }
      if (sharedRefsAbortRef.current) {
        sharedRefsAbortRef.current.abort();
        sharedRefsAbortRef.current = null;
      }
      if (compareAbortRef.current) {
        compareAbortRef.current.abort();
        compareAbortRef.current = null;
      }
    };
  }, []);

  const selectedDatabases = useMemo(
    () => selectedIds
      .map((id) => databases.find((db) => db.id === id))
      .filter(Boolean)
      .map((db) => ({ ...db, name: db.display_name || db.name || db.id })),
    [selectedIds, databases],
  );

  // Load each database's accessions once so downstream slots only offer comparable DBs.
  useEffect(() => {
    const requestId = ++accessionsRequestIdRef.current;
    if (accessionsAbortRef.current) {
      accessionsAbortRef.current.abort();
      accessionsAbortRef.current = null;
    }
    if (databases.length === 0) {
      setAccessionsByDb(null);
      return undefined;
    }
    const controller = new AbortController();
    accessionsAbortRef.current = controller;
    fetchReferenceAccessions(databases.map((db) => db.id), { signal: controller.signal })
      .then((data) => {
        if (
          !isMountedRef.current
          || requestId !== accessionsRequestIdRef.current
          || controller.signal.aborted
        ) {
          return;
        }
        setAccessionsByDb(data?.items || {});
      })
      .catch((err) => {
        if (
          !isMountedRef.current
          || requestId !== accessionsRequestIdRef.current
          || controller.signal.aborted
          || isAbortError(err)
        ) {
          return;
        }
        setAccessionsByDb(null);
      })
      .finally(() => {
        if (accessionsAbortRef.current === controller) {
          accessionsAbortRef.current = null;
        }
      });
    return () => {
      accessionsRequestIdRef.current += 1;
      controller.abort();
      if (accessionsAbortRef.current === controller) {
        accessionsAbortRef.current = null;
      }
    };
  }, [databases]);

  useEffect(() => {
    const selectionKey = selectedIds.join(',');
    if (selectedIds.length < MIN_COMPARE_DATABASES) {
      sharedRefsRequestIdRef.current += 1;
      if (sharedRefsAbortRef.current) {
        sharedRefsAbortRef.current.abort();
        sharedRefsAbortRef.current = null;
      }
      loadedRefsKeyRef.current = '';
      setSharedRefsLoading(false);
      setSharedRefs([]);
      setAccessionState('');
      return undefined;
    }
    if (loadedRefsKeyRef.current === selectionKey) {
      setSharedRefsLoading(false);
      return undefined;
    }
    const requestId = ++sharedRefsRequestIdRef.current;
    if (sharedRefsAbortRef.current) {
      sharedRefsAbortRef.current.abort();
    }
    const controller = new AbortController();
    sharedRefsAbortRef.current = controller;

    loadedRefsKeyRef.current = '';
    setSharedRefs([]);
    setAccessionState('');
    setSharedRefsLoading(true);
    setError('');
    fetchSharedReferences(selectedIds, { signal: controller.signal })
      .then((data) => {
        if (
          !isMountedRef.current
          || requestId !== sharedRefsRequestIdRef.current
          || controller.signal.aborted
        ) {
          return;
        }
        loadedRefsKeyRef.current = selectionKey;
        const items = Array.isArray(data?.items) ? [...data.items] : [];
        setSharedRefs(items);
        setAccessionState(items.length > 0 ? items[0].accession : '');
      })
      .catch((err) => {
        if (
          !isMountedRef.current
          || requestId !== sharedRefsRequestIdRef.current
          || controller.signal.aborted
          || isAbortError(err)
        ) {
          return;
        }
        loadedRefsKeyRef.current = '';
        setSharedRefs([]);
        setAccessionState('');
        setError(err?.message || 'Failed to load shared references.');
      })
      .finally(() => {
        if (sharedRefsAbortRef.current === controller) {
          sharedRefsAbortRef.current = null;
        }
        if (isMountedRef.current && requestId === sharedRefsRequestIdRef.current) {
          setSharedRefsLoading(false);
        }
      });
    return () => {
      sharedRefsRequestIdRef.current += 1;
      controller.abort();
      if (sharedRefsAbortRef.current === controller) {
        sharedRefsAbortRef.current = null;
      }
    };
  }, [selectedIds]);

  useEffect(() => {
    setCompareState({
      slots,
      accession,
      response,
      regionFilter,
      expandedKey,
      tableSearch,
      sharedRefs,
      sharedRefsKey: loadedRefsKeyRef.current,
    });
  }, [slots, accession, response, regionFilter, expandedKey, tableSearch, sharedRefs]);

  const invalidateCompare = useCallback(() => {
    compareRequestIdRef.current += 1;
    if (compareAbortRef.current) {
      compareAbortRef.current.abort();
      compareAbortRef.current = null;
    }
    setIsBusy(false);
  }, []);

  const handleSlotChange = useCallback((index, value) => {
    invalidateCompare();
    setError('');
    setSlots((current) => {
      const next = [...current];
      next[index] = value;
      for (let later = index + 1; later < next.length; later += 1) {
        next[later] = '';
      }
      return next;
    });
    setResponse(null);
    setRegionFilter('all');
    setExpandedKey('');
    setTableSearch('');
  }, [invalidateCompare]);

  const setAccession = useCallback((nextAccession) => {
    invalidateCompare();
    setError('');
    setAccessionState(nextAccession);
  }, [invalidateCompare]);

  const sharesAccession = useCallback(
    (candidateId, otherId) => {
      if (!accessionsByDb) return true;
      const candidate = accessionsByDb[candidateId] || {};
      const other = accessionsByDb[otherId] || {};
      return Object.keys(candidate).some((acc) => acc in other);
    },
    [accessionsByDb],
  );

  const optionsForSlot = (index) => {
    const chosen = slots.slice(0, index).filter(Boolean);
    return databases.filter((db) => {
      if (chosen.includes(db.id)) return false;
      if (chosen.length === 0) return true;
      return chosen.every((id) => sharesAccession(db.id, id));
    });
  };

  const runCompare = useCallback(async () => {
    if (selectedIds.length < MIN_COMPARE_DATABASES || !accession || sharedRefsLoading) {
      return;
    }
    const requestId = ++compareRequestIdRef.current;
    if (compareAbortRef.current) {
      compareAbortRef.current.abort();
    }
    const controller = new AbortController();
    compareAbortRef.current = controller;
    const requestKey = `${selectedIds.join(',')}::${accession}`;

    setIsBusy(true);
    setError('');
    try {
      const data = await compareDatabases(selectedIds, accession, { signal: controller.signal });
      if (
        !isMountedRef.current
        || requestId !== compareRequestIdRef.current
        || controller.signal.aborted
        || currentCompareKeyRef.current !== requestKey
      ) {
        return;
      }
      setResponse({ ...data, databases: selectedDatabases });
      setRegionFilter('all');
      setExpandedKey('');
      setTableSearch('');
    } catch (err) {
      if (
        !isMountedRef.current
        || requestId !== compareRequestIdRef.current
        || controller.signal.aborted
        || isAbortError(err)
        || currentCompareKeyRef.current !== requestKey
      ) {
        return;
      }
      setResponse(null);
      setError(err?.message || 'Comparison failed.');
    } finally {
      if (compareAbortRef.current === controller) {
        compareAbortRef.current = null;
      }
      if (isMountedRef.current && requestId === compareRequestIdRef.current) {
        setIsBusy(false);
      }
    }
  }, [selectedIds, selectedDatabases, accession, sharedRefsLoading]);

  // Keep reference identity stable so clicking/expanding rows does not recreate Venn.
  const handleRegionClick = useCallback((region) => {
    setRegionFilter(regionKey(region));
  }, []);

  const vennRegions = useMemo(
    () => (response ? (response.venn || []).filter((entry) => entry.count > 0) : []),
    [response],
  );

  const filteredRows = useMemo(() => {
    if (!response) return [];
    const rows = response.rows || [];
    const regionMatched = regionFilter === 'all'
      ? rows
      : rows.filter((row) => regionKey(row.region) === regionFilter);
    const term = tableSearch.trim().toLowerCase();
    if (!term) return regionMatched;
    return regionMatched.filter((row) => [
      row.feature,
      row.reference,
      row.position + 1,
      row.mutation,
      row.drug,
    ].some((value) => String(value ?? '').toLowerCase().includes(term)));
  }, [response, regionFilter, tableSearch]);

  const metadataFields = response?.metadata_fields
    ? METADATA_FIELDS.filter((field) => response.metadata_fields.includes(field.key))
    : METADATA_FIELDS;

  const exportTsv = () => {
    if (!response) return;
    const dbs = response.databases || [];
    const headers = ['feature', 'ref', 'pos', 'mut', 'database', ...metadataFields.map((f) => f.label)];
    const rows = [];
    for (const row of filteredRows) {
      for (const db of dbs) {
        const meta = row.per_db[db.id];
        rows.push([
          row.feature,
          row.reference,
          row.position + 1,
          row.mutation,
          db.name,
          ...metadataFields.map((f) => meta?.[f.key]),
        ]);
      }
    }
    downloadTsv(headers, rows, 'database-comparison.tsv');
  };

  const canCompare = (
    selectedIds.length >= MIN_COMPARE_DATABASES
    && Boolean(accession)
    && !sharedRefsLoading
    && !isBusy
  );

  return {
    slots,
    selectedIds,
    optionsForSlot,
    handleSlotChange,
    sharedRefs,
    sharedRefsLoading,
    accession,
    setAccession,
    response,
    isBusy,
    error,
    regionFilter,
    setRegionFilter,
    expandedKey,
    setExpandedKey,
    tableSearch,
    setTableSearch,
    vennRegions,
    filteredRows,
    metadataFields,
    canCompare,
    runCompare,
    handleRegionClick,
    exportTsv,
  };
}
