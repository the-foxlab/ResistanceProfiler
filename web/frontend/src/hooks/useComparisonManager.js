import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiPost, formatUserError, isAbortError } from '../api';
import { selectIndexedItems } from '../utils';

export function useComparisonManager({
  sessionResults,
  selectedResultIndices,
  setSelectedResultIndices,
  setStatusError,
}) {
  const [comparisonData, setComparisonData] = useState(null);
  const [isComparisonBusy, setIsComparisonBusy] = useState(false);
  const [nonSynonymousOnly, setNonSynonymousOnly] = useState(false);
  const [dbHitsOnly, setDbHitsOnly] = useState(false);

  const isMountedRef = useRef(true);
  const requestIdRef = useRef(0);
  const abortControllerRef = useRef(null);
  const hadCompareErrorRef = useRef(false);
  const setStatusErrorRef = useRef(setStatusError);
  const setSelectedResultIndicesRef = useRef(setSelectedResultIndices);
  setStatusErrorRef.current = setStatusError;
  setSelectedResultIndicesRef.current = setSelectedResultIndices;

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      requestIdRef.current += 1;
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
    };
  }, []);

  const firstSelected = useMemo(
    () => selectIndexedItems(sessionResults, selectedResultIndices)[0] || null,
    [selectedResultIndices, sessionResults],
  );

  const comparisonDbId = firstSelected?.database_id || null;
  const comparisonRefName = firstSelected?.reference_name || null;

  const selectAllComparable = useCallback(() => {
    if (!firstSelected) return;
    const targetDbId = firstSelected.database_id || '';
    const targetRefName = firstSelected.reference_name || '';
    const allMatching = new Set();
    sessionResults.forEach((result, idx) => {
      if ((result.database_id || '') === targetDbId && (result.reference_name || '') === targetRefName) {
        allMatching.add(idx);
      }
    });
    setSelectedResultIndicesRef.current(allMatching);
  }, [firstSelected, sessionResults]);

  const fetchComparisonData = useCallback(async (nonSynOnly, dbHitsOnlyOverride) => {
    const effectiveNonSyn = nonSynOnly !== undefined ? nonSynOnly : nonSynonymousOnly;
    const effectiveDbHits = dbHitsOnlyOverride !== undefined ? dbHitsOnlyOverride : dbHitsOnly;

    const selectedResults = selectIndexedItems(sessionResults, selectedResultIndices);
    if (selectedResults.length < 2) return;

    const artifactIds = selectedResults.map((result) => result.report_json_path).filter(Boolean);
    if (artifactIds.length < 2) return;

    const requestId = ++requestIdRef.current;
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    if (hadCompareErrorRef.current) {
      hadCompareErrorRef.current = false;
      setStatusErrorRef.current?.('');
    }

    setIsComparisonBusy(true);
    setComparisonData(null);
    try {
      const response = await apiPost(
        '/api/compare',
        {
          artifact_ids: artifactIds,
          non_synonymous_only: effectiveNonSyn,
          db_hits_only: effectiveDbHits,
        },
        { signal: controller.signal },
      );
      if (
        !isMountedRef.current
        || requestId !== requestIdRef.current
        || controller.signal.aborted
      ) {
        return;
      }
      setComparisonData(response);
    } catch (error) {
      if (
        !isMountedRef.current
        || requestId !== requestIdRef.current
        || controller.signal.aborted
        || isAbortError(error)
      ) {
        return;
      }
      hadCompareErrorRef.current = true;
      setStatusErrorRef.current?.(formatUserError(error?.message));
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
      }
      if (isMountedRef.current && requestId === requestIdRef.current) {
        setIsComparisonBusy(false);
      }
    }
  }, [nonSynonymousOnly, dbHitsOnly, sessionResults, selectedResultIndices]);

  const clearComparison = useCallback(() => {
    requestIdRef.current += 1;
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    if (hadCompareErrorRef.current) {
      hadCompareErrorRef.current = false;
      setStatusErrorRef.current?.('');
    }
    setIsComparisonBusy(false);
    setComparisonData(null);
    setSelectedResultIndicesRef.current(new Set());
  }, []);

  return {
    comparisonData,
    isComparisonBusy,
    nonSynonymousOnly,
    setNonSynonymousOnly,
    dbHitsOnly,
    setDbHitsOnly,
    comparisonDbId,
    comparisonRefName,
    selectAllComparable,
    fetchComparisonData,
    clearComparison,
  };
}
