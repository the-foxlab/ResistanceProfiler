import { useMemo, useState } from 'react';
import { apiPost, formatUserError } from '../api';
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

  const firstSelected = useMemo(() => {
    const firstIdx = selectedResultIndices.values().next().value;
    return firstIdx === undefined ? null : sessionResults[firstIdx] || null;
  }, [selectedResultIndices, sessionResults]);

  const comparisonDbId = firstSelected?.database_id || null;
  const comparisonRefName = firstSelected?.reference_name || null;

  const selectAllComparable = () => {
    if (!firstSelected) return;
    const allMatching = new Set();
    sessionResults.forEach((result, idx) => {
      if (result.database_id === firstSelected.database_id && result.reference_name === firstSelected.reference_name) {
        allMatching.add(idx);
      }
    });
    setSelectedResultIndices(allMatching);
  };

  const fetchComparisonData = async (nonSynOnly, dbHitsOnlyOverride) => {
    const effectiveNonSyn = nonSynOnly !== undefined ? nonSynOnly : nonSynonymousOnly;
    const effectiveDbHits = dbHitsOnlyOverride !== undefined ? dbHitsOnlyOverride : dbHitsOnly;

    const selectedResults = selectIndexedItems(sessionResults, selectedResultIndices);
    if (selectedResults.length < 2) return;

    const artifactIds = selectedResults.map((result) => result.report_json_path).filter(Boolean);
    if (artifactIds.length < 2) return;

    setIsComparisonBusy(true);
    setComparisonData(null);
    try {
      const response = await apiPost('/api/compare', {
        artifact_ids: artifactIds,
        non_synonymous_only: effectiveNonSyn,
        db_hits_only: effectiveDbHits,
      });
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
