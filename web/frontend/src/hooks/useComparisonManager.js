import { useMemo, useState } from 'react';
import { apiPost, formatUserError } from '../api';

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

  // Derived: which database the selected comparison results share
  const comparisonDbId = useMemo(() => {
    const indices = [...selectedResultIndices];
    if (indices.length === 0) return null;
    return sessionResults[indices[0]]?.database_id || null;
  }, [selectedResultIndices, sessionResults]);

  // Derived: which reference the selected comparison results share
  const comparisonRefName = useMemo(() => {
    const indices = [...selectedResultIndices];
    if (indices.length === 0) return null;
    return sessionResults[indices[0]]?.reference_name || null;
  }, [selectedResultIndices, sessionResults]);

  const selectAllComparable = () => {
    if (selectedResultIndices.size === 0) return;
    const firstIdx = [...selectedResultIndices][0];
    const firstResult = sessionResults[firstIdx];
    if (!firstResult) return;
    const targetDbId = firstResult.database_id;
    const targetRefName = firstResult.reference_name;
    const allMatching = new Set();
    sessionResults.forEach((result, idx) => {
      if (result.database_id === targetDbId && result.reference_name === targetRefName) {
        allMatching.add(idx);
      }
    });
    setSelectedResultIndices(allMatching);
  };

  const fetchComparisonData = async (nonSynOnly, dbHitsOnlyOverride) => {
    const effectiveNonSyn = nonSynOnly !== undefined ? nonSynOnly : nonSynonymousOnly;
    const effectiveDbHits = dbHitsOnlyOverride !== undefined ? dbHitsOnlyOverride : dbHitsOnly;

    const selectedResults = [...selectedResultIndices]
      .map((i) => sessionResults[i])
      .filter(Boolean);

    if (selectedResults.length < 2) return;

    const artifactIds = selectedResults
      .map((r) => r.report_json_path)
      .filter(Boolean);

    if (artifactIds.length < 2) return;

    setIsComparisonBusy(true);
    setComparisonData(null);
    try {
      const response = await apiPost('/api/compare', { artifact_ids: artifactIds, non_synonymous_only: effectiveNonSyn, db_hits_only: effectiveDbHits });
      setComparisonData(response);
    } catch (error) {
      setStatusError(formatUserError(error.message));
    } finally {
      setIsComparisonBusy(false);
    }
  };

  const clearComparison = () => {
    setComparisonData(null);
    setSelectedResultIndices(new Set());
  };

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
