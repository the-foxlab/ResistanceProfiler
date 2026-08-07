import { useEffect, useState } from 'react';
import faviconSrc from './assets/favicon.svg';
import { FRONTEND_CONFIG } from './config';
import { API_BASE, API_TOKEN, buildHeaders, apiGet } from './api';
import { PROFILE_MODES } from './constants';
import { useProfileSubmit } from './hooks/useProfileSubmit';
import { useBatchManager } from './hooks/useBatchManager';
import { useMutationBrowser } from './hooks/useMutationBrowser';
import { useSessionResults } from './hooks/useSessionResults';
import { useUploadManager } from './hooks/useUploadManager';
import { useComparisonManager } from './hooks/useComparisonManager';

// Re-export existing public API for backward compatibility
export { buildApiUrl, formatUserError, apiPostRaw } from './api';
export { PROFILE_MODES };

export function useDashboardLogic() {
  // Top-level orchestration state not owned by any domain hook.
  const [databases, setDatabases] = useState([]);
  const [selectedDatabaseId, setSelectedDatabaseId] = useState('');
  const [statusError, setStatusError] = useState('');
  // ``legalLink`` is null when the imprint feature is disabled; otherwise it is the
  // href the footer "Legal notice" link should point at — an external URL when the
  // backend reports ``kind:'url'``, or the self-hosted ``/legal`` route for path mode.
  const [legalLink, setLegalLink] = useState(null);
  const [resproVersion, setResproVersion] = useState(null);
  const [activeMode, setActiveMode] = useState('analyze');
  const [activeProfileMode, setActiveProfileMode] = useState('vcf');
  const [analyzeSubMode, setAnalyzeSubMode] = useState('single');

  const selectedDatabase = databases.find((item) => item.id === selectedDatabaseId) || null;

  // Compose domain hooks.
  const session = useSessionResults();
  const upload = useUploadManager();
  const mutations = useMutationBrowser({ selectedDatabaseId, setStatusError });
  const profile = useProfileSubmit({
    setStatusError,
    addReportPath: session.addReportPath,
    addUploadedPath: session.addUploadedPath,
    addResultArtifactPaths: session.addResultArtifactPaths,
    setSessionResults: session.setSessionResults,
    setSelectedProfileReportPath: session.setSelectedProfileReportPath,
    setInlineReportPath: session.setInlineReportPath,
    setInlineReportLabel: session.setInlineReportLabel,
    databases,
    selectedDatabaseId,
    activeProfileMode,
    analyzeSubMode,
    setUploadProgress: upload.setUploadProgress,
  });
  const batch = useBatchManager({
    selectedDatabaseId,
    addReportPath: session.addReportPath,
    addUploadedPath: session.addUploadedPath,
    addResultArtifactPaths: session.addResultArtifactPaths,
    setSessionResults: session.setSessionResults,
    setUploadProgress: upload.setUploadProgress,
    setStatusError,
  });
  const comparison = useComparisonManager({
    sessionResults: session.sessionResults,
    selectedResultIndices: session.selectedResultIndices,
    setSelectedResultIndices: session.setSelectedResultIndices,
    setStatusError,
  });

  // Database loading effect (top-level orchestration).
  useEffect(() => {
    const initData = async () => {
      try {
        const uiConfigPayload = await apiGet('/api/ui/config').catch(() => null);
        const uiConfig = uiConfigPayload?.data || {};
        if (Number.isFinite(uiConfig.batch_max_samples) && uiConfig.batch_max_samples > 0) {
          batch.setBatchMaxSamples(uiConfig.batch_max_samples);
        }
        if (Number.isFinite(uiConfig.sample_limit_per_minute) && uiConfig.sample_limit_per_minute > 0) {
          batch.setSampleLimitPerMinute(uiConfig.sample_limit_per_minute);
        }
        if (uiConfig.version) {
          setResproVersion(uiConfig.version);
        }
        const legalPayload = await apiGet('/api/ui/legal').catch(() => null);
        setLegalLink(_resolveLegalLink(legalPayload?.data));
        const payload = await apiGet('/api/databases');
        const items = payload.data.items || [];
        setDatabases(items);
        if (items.length > 0) {
          setSelectedDatabaseId(items[0].id);
          return;
        }
      } catch (error) {
        setStatusError(`Error loading data: ${error.message}`);
      }
    };
    initData();
  }, []);

  // Mutation loading effect — reload when database changes.
  useEffect(() => {
    if (!selectedDatabaseId) {
      return;
    }
    mutations.loadMutations(selectedDatabaseId);
  }, [selectedDatabase, selectedDatabaseId]);

  // Report path sync effect.
  useEffect(() => {
    if (!session.selectedProfileReportPath) {
      return;
    }
    const selectedOption = session.reportOptions.find((option) => option.path === session.selectedProfileReportPath);
    session.setInlineReportPath(session.selectedProfileReportPath);
    session.setInlineReportLabel(selectedOption ? selectedOption.label : 'Selected report');
  }, [session.reportOptions, session.selectedProfileReportPath]);

  // Session cleanup effect (cross-cutting concern using uploadedPaths, reportPaths).
  useEffect(() => {
    const cleanupUrl = `${API_BASE}/api/session/cleanup`;

    const handlePageHide = () => {
      if (session.uploadedPaths.length === 0 && session.reportPaths.length === 0) {
        return;
      }
      const payload = JSON.stringify({
        upload_ids: session.uploadedPaths,
        artifact_ids: session.reportPaths,
        ...(API_TOKEN ? { token: API_TOKEN } : {}),
      });
      if (navigator.sendBeacon) {
        const blob = new Blob([payload], { type: 'application/json' });
        navigator.sendBeacon(cleanupUrl, blob);
        return;
      }
      fetch(cleanupUrl, {
        method: 'POST',
        headers: buildHeaders({ 'Content-Type': 'application/json' }),
        body: payload,
        keepalive: true,
      }).catch(() => {
        // Ignore cleanup errors during page unload.
      });
    };

    window.addEventListener('pagehide', handlePageHide);
    return () => {
      window.removeEventListener('pagehide', handlePageHide);
    };
  }, [session.uploadedPaths, session.reportPaths]);

  // Favicon effect.
  useEffect(() => {
    // Ensure the app uses the bundled SVG favicon in all entry modes.
    let link = document.querySelector('link[rel="icon"]');
    if (!link) {
      link = document.createElement('link');
      link.setAttribute('rel', 'icon');
      document.head.appendChild(link);
    }
    link.setAttribute('type', 'image/svg+xml');
    link.setAttribute('href', faviconSrc);
  }, []);

  return {
    // Expose one stable object so the view component stays presentation-focused.
    API_BASE,
    PROFILE_MODES,
    // Profile submit
    vcfInput: profile.vcfInput,
    setVcfInput: profile.setVcfInput,
    fastaInput: profile.fastaInput,
    setFastaInput: profile.setFastaInput,
    rules: mutations.rules,
    formulaRules: mutations.formulaRules,
    databases,
    selectedDatabase,
    selectedDatabaseId,
    setSelectedDatabaseId,
    statusError,
    legalLink,
    resproVersion,
    selectedProfileReportPath: session.selectedProfileReportPath,
    setSelectedProfileReportPath: session.setSelectedProfileReportPath,
    mutationFilter: mutations.mutationFilter,
    setMutationFilter: mutations.setMutationFilter,
    mutationFilterColumn: mutations.mutationFilterColumn,
    setMutationFilterColumn: mutations.setMutationFilterColumn,
    mutationSortColumn: mutations.mutationSortColumn,
    setMutationSortColumn: mutations.setMutationSortColumn,
    mutationSortAsc: mutations.mutationSortAsc,
    setMutationSortAsc: mutations.setMutationSortAsc,
    formulaFilter: mutations.formulaFilter,
    setFormulaFilter: mutations.setFormulaFilter,
    formulaFilterColumn: mutations.formulaFilterColumn,
    setFormulaFilterColumn: mutations.setFormulaFilterColumn,
    mutationsLoaded: mutations.mutationsLoaded,
    activeMode,
    setActiveMode,
    activeProfileMode,
    setActiveProfileMode,
    analyzeSubMode,
    setAnalyzeSubMode,
    sessionResults: session.sessionResults,
    inlineReportPath: session.inlineReportPath,
    inlineReportLabel: session.inlineReportLabel,
    mutationColumns: mutations.mutationColumns,
    formulaColumns: mutations.formulaColumns,
    mutationPlotMeta: mutations.mutationPlotMeta,
    displayedRules: mutations.displayedRules,
    displayedFormulaRules: mutations.displayedFormulaRules,
    reportOptions: session.reportOptions,
    isProfileBusy: profile.isProfileBusy,
    canCancelJob: profile.canCancelJob,
    activeJobStatus: profile.activeJobStatus,
    isCancelingJob: profile.isCancelingJob,
    cancelActiveJob: profile.cancelActiveJob,
    runSelectedProfile: profile.runSelectedProfile,
    buildReportUrl: profile.buildReportUrl,
    buildArtifactUrl: profile.buildArtifactUrl,
    uploadFastaFile: profile.uploadFastaFile,
    uploadVcfFile: profile.uploadVcfFile,
    uploadReferenceFile: profile.uploadReferenceFile,
    uploadBamFile: profile.uploadBamFile,
    uploadJsonFile: profile.uploadJsonFile,
    jsonInputId: profile.jsonInputId,
    isRegenerateBusy: profile.isProcessingRegenerate,
    runRegenerateFromJson: profile.runRegenerateFromJson,
    downloadMutationsAsTsv: mutations.downloadMutationsAsTsv,
    downloadFormulaRulesAsTsv: mutations.downloadFormulaRulesAsTsv,
    uploadProgress: upload.uploadProgress,
    // Batch
    batchMode: batch.batchMode,
    setBatchMode: batch.setBatchMode,
    batchVcfFiles: batch.batchVcfFiles,
    batchFastaFiles: batch.batchFastaFiles,
    batchReferenceFasta: batch.batchReferenceFasta,
    batchSamples: batch.batchSamples,
    batchSubmitting: batch.batchSubmitting,
    isBatchDownloadBusy: batch.isBatchDownloadBusy,
    batchError: batch.batchError,
    batchRateLimitCooldown: batch.batchRateLimitCooldown,
    setBatchRateLimitCooldown: batch.setBatchRateLimitCooldown,
    batchSubmitted: batch.batchSubmitted,
    batchMaxSamples: batch.batchMaxSamples,
    sampleLimitPerMinute: batch.sampleLimitPerMinute,
    batchVcfCutoffs: batch.batchVcfCutoffs,
    setBatchVcfCutoffs: batch.setBatchVcfCutoffs,
    addBatchVcfFiles: batch.addBatchVcfFiles,
    addBatchFastaFiles: batch.addBatchFastaFiles,
    addBatchBamFiles: batch.addBatchBamFiles,
    attachBatchBam: batch.attachBatchBam,
    removeBatchFile: batch.removeBatchFile,
    uploadBatchReferenceFasta: batch.uploadBatchReferenceFasta,
    submitBatch: batch.submitBatch,
    downloadAllBatchArtifacts: batch.downloadAllBatchArtifacts,
    resetBatch: batch.resetBatch,
    // Session
    isSessionDownloadBusy: session.isSessionDownloadBusy,
    downloadAllSessionArtifacts: () => session.downloadAllSessionArtifacts(setStatusError),
    // Comparison
    selectedResultIndices: session.selectedResultIndices,
    setSelectedResultIndices: session.setSelectedResultIndices,
    comparisonDbId: comparison.comparisonDbId,
    comparisonRefName: comparison.comparisonRefName,
    selectAllComparable: comparison.selectAllComparable,
    comparisonData: comparison.comparisonData,
    isComparisonBusy: comparison.isComparisonBusy,
    nonSynonymousOnly: comparison.nonSynonymousOnly,
    setNonSynonymousOnly: comparison.setNonSynonymousOnly,
    dbHitsOnly: comparison.dbHitsOnly,
    setDbHitsOnly: comparison.setDbHitsOnly,
    fetchComparisonData: comparison.fetchComparisonData,
    downloadSelectedArtifacts: () => session.downloadSelectedArtifacts(setStatusError),
    clearComparison: comparison.clearComparison,
  };
}

/**
 * Resolve the footer "Legal notice" href from the ``/api/ui/legal`` payload.
 *
 * Returns ``null`` when the imprint feature is disabled. For ``kind:'url'`` the
 * external URL is returned so the footer links straight to the hosted imprint. For
 * ``kind:'path'`` (or a stale backend that omits ``kind``) the self-hosted ``/legal``
 * route is returned. A disabled payload without ``kind`` is treated as path mode.
 */
export function _resolveLegalLink(legalData) {
  if (!legalData || !legalData.enabled) {
    return null;
  }
  if (legalData.kind === 'url' && legalData.url) {
    return legalData.url;
  }
  return `${API_BASE}/legal`;
}

