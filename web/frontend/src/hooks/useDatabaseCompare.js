import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { fetchSharedReferences, fetchReferenceAccessions, compareDatabases } from '../api';
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
  const [accession, setAccession] = useState(persisted.accession);
  const [response, setResponse] = useState(persisted.response);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState('');
  const [regionFilter, setRegionFilter] = useState(persisted.regionFilter);
  const [expandedKey, setExpandedKey] = useState(persisted.expandedKey);
  const [tableSearch, setTableSearch] = useState(persisted.tableSearch);
  const loadedRefsKeyRef = useRef(persisted.sharedRefsKey);

  const selectedIds = useMemo(() => slots.filter(Boolean), [slots]);

  const selectedDatabases = useMemo(
    () => selectedIds
      .map((id) => databases.find((db) => db.id === id))
      .filter(Boolean)
      .map((db) => ({ ...db, name: db.display_name || db.name || db.id })),
    [selectedIds, databases],
  );

  // Load each database's accessions once so downstream slots only offer comparable DBs.
  useEffect(() => {
    if (databases.length === 0) {
      setAccessionsByDb(null);
      return undefined;
    }
    let cancelled = false;
    fetchReferenceAccessions(databases.map((db) => db.id))
      .then((data) => {
        if (!cancelled) setAccessionsByDb(data.items || {});
      })
      .catch(() => {
        if (!cancelled) setAccessionsByDb(null);
      });
    return () => {
      cancelled = true;
    };
  }, [databases]);

  useEffect(() => {
    const selectionKey = selectedIds.join(',');
    if (selectedIds.length < MIN_COMPARE_DATABASES) {
      loadedRefsKeyRef.current = '';
      setSharedRefs([]);
      setAccession('');
      return undefined;
    }
    if (loadedRefsKeyRef.current === selectionKey) {
      return undefined;
    }
    loadedRefsKeyRef.current = selectionKey;
    let cancelled = false;
    setSharedRefsLoading(true);
    setError('');
    fetchSharedReferences(selectedIds)
      .then((data) => {
        if (cancelled) return;
        const items = data.items || [];
        setSharedRefs(items);
        setAccession(items.length > 0 ? items[0].accession : '');
      })
      .catch((err) => {
        if (cancelled) return;
        setSharedRefs([]);
        setAccession('');
        setError(err.message || 'Failed to load shared references.');
      })
      .finally(() => {
        if (!cancelled) setSharedRefsLoading(false);
      });
    return () => {
      cancelled = true;
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

  const handleSlotChange = (index, value) => {
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
  };

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
    if (selectedIds.length < MIN_COMPARE_DATABASES || !accession) {
      return;
    }
    setIsBusy(true);
    setError('');
    try {
      const data = await compareDatabases(selectedIds, accession);
      setResponse({ ...data, databases: selectedDatabases });
      setRegionFilter('all');
      setExpandedKey('');
      setTableSearch('');
    } catch (err) {
      setResponse(null);
      setError(err.message || 'Comparison failed.');
    } finally {
      setIsBusy(false);
    }
  }, [selectedIds, selectedDatabases, accession]);

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

  const canCompare = selectedIds.length >= MIN_COMPARE_DATABASES && Boolean(accession) && !isBusy;

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
