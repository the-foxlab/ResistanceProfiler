import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useNavigate } from 'react-router';

import faviconSrc from './assets/favicon.svg';
import {
  apiGet,
  buildArtifactUrl,
  buildReportUrl,
  isAbortError,
  setArtifactBundleLimit,
} from './api';
import { PROFILE_MODES } from './constants';
import { useBatchManager } from './hooks/useBatchManager';
import { useComparisonManager } from './hooks/useComparisonManager';
import { useDatabaseRules, useMutationTableFilter } from './hooks/useMutationBrowser';
import { useProfileSubmit } from './hooks/useProfileSubmit';
import { useSessionCleanup } from './hooks/useSessionCleanup';
import { useSessionResults } from './hooks/useSessionResults';
import { useUploadManager } from './hooks/useUploadManager';
import {
  _resolveContactEmail,
  _resolveLegalLink,
  webVersion,
} from './useDashboardLogic';

const StatusErrorValueContext = createContext(null);
const StatusErrorActionContext = createContext(null);

const CoreDatabasesContext = createContext(null);
const CoreSelectedDbContext = createContext(null);
const CoreFooterContext = createContext(null);
const CoreAnalyzeModeContext = createContext(null);
const CoreActionsContext = createContext(null);
const CoreBatchConfigContext = createContext(null);

const SessionActionsContext = createContext(null);
const SessionReportSelectionContext = createContext(null);
const SessionResultsTableContext = createContext(null);

const UploadProgressStoreContext = createContext(null);
const UploadBusyContext = createContext(null);

const ProfileBusyContext = createContext(null);
const ProfilePanelContext = createContext(null);

const BatchBusyContext = createContext(null);
const BatchHeaderContext = createContext(null);
const BatchPanelContext = createContext(null);

const DatabaseRulesContext = createContext(null);
const MutationsFilterContext = createContext(null);
const ComparisonContext = createContext(null);
const ShellContext = createContext(null);

function StatusErrorProvider({ children }) {
  const [statusError, setStatusError] = useState('');
  return (
    <StatusErrorActionContext.Provider value={setStatusError}>
      <StatusErrorValueContext.Provider value={statusError}>
        {children}
      </StatusErrorValueContext.Provider>
    </StatusErrorActionContext.Provider>
  );
}

function CoreShellProvider({ children }) {
  const setStatusError = useContext(StatusErrorActionContext);
  const [databases, setDatabases] = useState([]);
  const [selectedDatabaseId, setSelectedDatabaseId] = useState('');
  const [legalLink, setLegalLink] = useState(null);
  const [contactEmail, setContactEmail] = useState(null);
  const [cliVersion, setCliVersion] = useState(null);
  const [activeProfileMode, setActiveProfileMode] = useState('vcf');
  const [analyzeSubMode, setAnalyzeSubMode] = useState('single');
  const [batchConfig, setBatchConfig] = useState({
    batchMaxSamples: null,
    sampleLimitPerMinute: null,
  });

  const navigate = useNavigate();
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;
  const stableNavigate = useCallback((...args) => navigateRef.current(...args), []);

  const selectedDatabase = useMemo(
    () => databases.find((item) => item.id === selectedDatabaseId) || null,
    [databases, selectedDatabaseId],
  );

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;

    const initData = async () => {
      const [configRes, legalRes, contactRes, databasesRes] = await Promise.allSettled([
        apiGet('/api/ui/config', { signal }),
        apiGet('/api/ui/legal', { signal }),
        apiGet('/api/ui/contact', { signal }),
        apiGet('/api/databases', { signal }),
      ]);
      if (signal.aborted) return;

      if (configRes.status === 'fulfilled') {
        const uiConfig = configRes.value?.data || {};
        const nextMax = Number.isFinite(uiConfig.batch_max_samples) && uiConfig.batch_max_samples > 0
          ? uiConfig.batch_max_samples
          : null;
        const nextLimit = Number.isFinite(uiConfig.sample_limit_per_minute) && uiConfig.sample_limit_per_minute > 0
          ? uiConfig.sample_limit_per_minute
          : null;
        if (nextMax !== null || nextLimit !== null) {
          setBatchConfig({ batchMaxSamples: nextMax, sampleLimitPerMinute: nextLimit });
        }
        if (Number.isInteger(uiConfig.artifact_bundle_max_artifacts) && uiConfig.artifact_bundle_max_artifacts > 0) {
          setArtifactBundleLimit(uiConfig.artifact_bundle_max_artifacts);
        }
        if (uiConfig.cli_version) setCliVersion(uiConfig.cli_version);
      }

      if (legalRes.status === 'fulfilled') {
        setLegalLink(_resolveLegalLink(legalRes.value?.data));
      } else if (!isAbortError(legalRes.reason)) {
        setLegalLink(null);
      }

      if (contactRes.status === 'fulfilled') {
        setContactEmail(_resolveContactEmail(contactRes.value?.data));
      } else if (!isAbortError(contactRes.reason)) {
        setContactEmail(null);
      }

      if (databasesRes.status === 'fulfilled') {
        const items = databasesRes.value?.data?.items || [];
        setDatabases(items);
        if (items.length > 0) setSelectedDatabaseId(items[0].id);
      } else if (!isAbortError(databasesRes.reason)) {
        const message = databasesRes.reason?.message || 'Failed to load databases.';
        setStatusError(`Error loading data: ${message}`);
      }
    };

    initData();
    return () => {
      controller.abort();
    };
  }, [setStatusError]);

  useEffect(() => {
    let link = document.querySelector('link[rel="icon"]');
    if (!link) {
      link = document.createElement('link');
      link.setAttribute('rel', 'icon');
      document.head.appendChild(link);
    }
    link.setAttribute('type', 'image/svg+xml');
    link.setAttribute('href', faviconSrc);
  }, []);

  const databasesValue = useMemo(
    () => ({ databases, setSelectedDatabaseId }),
    [databases],
  );
  const selectedDbValue = useMemo(
    () => ({ selectedDatabase, selectedDatabaseId, setSelectedDatabaseId }),
    [selectedDatabase, selectedDatabaseId],
  );
  const footerValue = useMemo(
    () => ({ legalLink, contactEmail, cliVersion, webVersion }),
    [legalLink, contactEmail, cliVersion],
  );
  const analyzeModeValue = useMemo(
    () => ({
      activeProfileMode,
      setActiveProfileMode,
      analyzeSubMode,
      setAnalyzeSubMode,
      PROFILE_MODES,
    }),
    [activeProfileMode, analyzeSubMode],
  );
  const actionsValue = useMemo(
    () => ({
      navigate: stableNavigate,
      setActiveProfileMode,
      setAnalyzeSubMode,
    }),
    [stableNavigate],
  );

  return (
    <CoreActionsContext.Provider value={actionsValue}>
      <CoreDatabasesContext.Provider value={databasesValue}>
        <CoreSelectedDbContext.Provider value={selectedDbValue}>
          <CoreFooterContext.Provider value={footerValue}>
            <CoreAnalyzeModeContext.Provider value={analyzeModeValue}>
              <CoreBatchConfigContext.Provider value={batchConfig}>
                {children}
              </CoreBatchConfigContext.Provider>
            </CoreAnalyzeModeContext.Provider>
          </CoreFooterContext.Provider>
        </CoreSelectedDbContext.Provider>
      </CoreDatabasesContext.Provider>
    </CoreActionsContext.Provider>
  );
}

function SessionProvider({ children }) {
  const setStatusError = useContext(StatusErrorActionContext);
  const session = useSessionResults();
  useSessionCleanup(session.uploadedPaths, session.reportPaths);

  const downloadRef = useRef(session.downloadSelectedArtifacts);
  downloadRef.current = session.downloadSelectedArtifacts;
  const stableDownloadSelectedArtifacts = useCallback(
    () => downloadRef.current(setStatusError),
    [setStatusError],
  );

  const actionsValue = useMemo(
    () => ({
      addUploadedPath: session.addUploadedPath,
      addResultArtifactPaths: session.addResultArtifactPaths,
      setSessionResults: session.setSessionResults,
      setSelectedProfileReportPath: session.setSelectedProfileReportPath,
      setInlineReportPath: session.setInlineReportPath,
      buildReportUrl,
      buildArtifactUrl,
    }),
    [
      session.addUploadedPath,
      session.addResultArtifactPaths,
      session.setSessionResults,
      session.setSelectedProfileReportPath,
      session.setInlineReportPath,
    ],
  );

  const reportSelectionValue = useMemo(
    () => ({
      selectedProfileReportPath: session.selectedProfileReportPath,
      setSelectedProfileReportPath: session.setSelectedProfileReportPath,
      inlineReportPath: session.inlineReportPath,
      reportOptions: session.reportOptions,
    }),
    [
      session.selectedProfileReportPath,
      session.setSelectedProfileReportPath,
      session.inlineReportPath,
      session.reportOptions,
    ],
  );

  const resultsTableValue = useMemo(
    () => ({
      sessionResults: session.sessionResults,
      selectedResultIndices: session.selectedResultIndices,
      setSelectedResultIndices: session.setSelectedResultIndices,
      isSessionDownloadBusy: session.isSessionDownloadBusy,
      downloadSelectedArtifacts: stableDownloadSelectedArtifacts,
    }),
    [
      session.sessionResults,
      session.selectedResultIndices,
      session.setSelectedResultIndices,
      session.isSessionDownloadBusy,
      stableDownloadSelectedArtifacts,
    ],
  );

  return (
    <SessionActionsContext.Provider value={actionsValue}>
      <SessionReportSelectionContext.Provider value={reportSelectionValue}>
        <SessionResultsTableContext.Provider value={resultsTableValue}>
          {children}
        </SessionResultsTableContext.Provider>
      </SessionReportSelectionContext.Provider>
    </SessionActionsContext.Provider>
  );
}

function UploadProvider({ children }) {
  const upload = useUploadManager({ syncProgress: false });

  const busyValue = useMemo(
    () => ({
      isUploading: upload.isUploading,
      beginUpload: upload.beginUpload,
      endUpload: upload.endUpload,
      setUploadProgress: upload.setUploadProgress,
    }),
    [upload.isUploading, upload.beginUpload, upload.endUpload, upload.setUploadProgress],
  );

  return (
    <UploadProgressStoreContext.Provider value={upload.progressStore}>
      <UploadBusyContext.Provider value={busyValue}>
        {children}
      </UploadBusyContext.Provider>
    </UploadProgressStoreContext.Provider>
  );
}

function ProfileProvider({ children }) {
  const setStatusError = useContext(StatusErrorActionContext);
  const { databases } = useContext(CoreDatabasesContext);
  const { selectedDatabaseId } = useContext(CoreSelectedDbContext);
  const { activeProfileMode } = useContext(CoreAnalyzeModeContext);
  const sessionActions = useContext(SessionActionsContext);
  const uploadBusy = useContext(UploadBusyContext);

  const profile = useProfileSubmit({
    setStatusError,
    addUploadedPath: sessionActions.addUploadedPath,
    addResultArtifactPaths: sessionActions.addResultArtifactPaths,
    setSessionResults: sessionActions.setSessionResults,
    setSelectedProfileReportPath: sessionActions.setSelectedProfileReportPath,
    setInlineReportPath: sessionActions.setInlineReportPath,
    databases,
    selectedDatabaseId,
    activeProfileMode,
    setUploadProgress: uploadBusy.setUploadProgress,
    beginUpload: uploadBusy.beginUpload,
    endUpload: uploadBusy.endUpload,
  });

  const runExampleProfileRef = useRef(profile.runExampleProfile);
  runExampleProfileRef.current = profile.runExampleProfile;
  const stableRunExampleProfile = useCallback(
    (...args) => runExampleProfileRef.current(...args),
    [],
  );

  const cancelUploadRef = useRef(profile.cancelUpload);
  cancelUploadRef.current = profile.cancelUpload;
  const stableCancelUpload = useCallback(
    (...args) => cancelUploadRef.current(...args),
    [],
  );

  const busyValue = useMemo(
    () => ({
      isProfileBusy: profile.isProfileBusy,
      isRegenerateBusy: profile.isRegenerateBusy,
      runExampleProfile: stableRunExampleProfile,
      cancelUpload: stableCancelUpload,
    }),
    [profile.isProfileBusy, profile.isRegenerateBusy, stableRunExampleProfile, stableCancelUpload],
  );

  return (
    <ProfileBusyContext.Provider value={busyValue}>
      <ProfilePanelContext.Provider value={profile}>
        {children}
      </ProfilePanelContext.Provider>
    </ProfileBusyContext.Provider>
  );
}

function BatchProvider({ children }) {
  const { selectedDatabaseId } = useContext(CoreSelectedDbContext);
  const batchConfig = useContext(CoreBatchConfigContext);
  const sessionActions = useContext(SessionActionsContext);
  const uploadBusy = useContext(UploadBusyContext);

  const batch = useBatchManager({
    selectedDatabaseId,
    addUploadedPath: sessionActions.addUploadedPath,
    addResultArtifactPaths: sessionActions.addResultArtifactPaths,
    setSessionResults: sessionActions.setSessionResults,
    setUploadProgress: uploadBusy.setUploadProgress,
  });

  useEffect(() => {
    if (batchConfig.batchMaxSamples !== null) {
      batch.setBatchMaxSamples(batchConfig.batchMaxSamples);
    }
  }, [batchConfig.batchMaxSamples, batch.setBatchMaxSamples]);

  useEffect(() => {
    if (batchConfig.sampleLimitPerMinute !== null) {
      batch.setSampleLimitPerMinute(batchConfig.sampleLimitPerMinute);
    }
  }, [batchConfig.sampleLimitPerMinute, batch.setSampleLimitPerMinute]);

  const setBatchModeRef = useRef(batch.setBatchMode);
  setBatchModeRef.current = batch.setBatchMode;
  const stableSetBatchMode = useCallback(
    (...args) => setBatchModeRef.current(...args),
    [],
  );

  const cancelBatchUploadRef = useRef(batch.cancelBatchUpload);
  cancelBatchUploadRef.current = batch.cancelBatchUpload;
  const stableCancelBatchUpload = useCallback(
    (...args) => cancelBatchUploadRef.current(...args),
    [],
  );

  const busyValue = useMemo(
    () => ({
      batchSubmitting: batch.batchSubmitting,
      isBatchUploading: batch.isBatchUploading,
    }),
    [batch.batchSubmitting, batch.isBatchUploading],
  );

  const headerValue = useMemo(
    () => ({
      batchMode: batch.batchMode,
      setBatchMode: stableSetBatchMode,
      batchSubmitted: batch.batchSubmitted,
      batchSubmitting: batch.batchSubmitting,
      isBatchUploading: batch.isBatchUploading,
      cancelBatchUpload: stableCancelBatchUpload,
    }),
    [
      batch.batchMode,
      stableSetBatchMode,
      batch.batchSubmitted,
      batch.batchSubmitting,
      batch.isBatchUploading,
      stableCancelBatchUpload,
    ],
  );

  return (
    <BatchBusyContext.Provider value={busyValue}>
      <BatchHeaderContext.Provider value={headerValue}>
        <BatchPanelContext.Provider value={batch}>
          {children}
        </BatchPanelContext.Provider>
      </BatchHeaderContext.Provider>
    </BatchBusyContext.Provider>
  );
}

function DatabaseRulesProvider({ children }) {
  const setStatusError = useContext(StatusErrorActionContext);
  const { selectedDatabaseId } = useContext(CoreSelectedDbContext);
  const [switchTick, setSwitchTick] = useState(0);

  const handleDatabaseSwitch = useCallback(() => {
    setSwitchTick((tick) => tick + 1);
  }, []);

  const dbRules = useDatabaseRules({
    setStatusError,
    onDatabaseSwitch: handleDatabaseSwitch,
  });

  useEffect(() => {
    const controller = new AbortController();
    dbRules.loadMutations(selectedDatabaseId, { signal: controller.signal });
    return () => controller.abort();
  }, [selectedDatabaseId, dbRules.loadMutations]);

  const rulesValue = useMemo(
    () => ({
      rules: dbRules.rules,
      formulaRules: dbRules.formulaRules,
      mutationPlotMeta: dbRules.mutationPlotMeta,
      mutationsLoaded: dbRules.mutationsLoaded,
      isMutationsLoading: dbRules.isMutationsLoading,
      loadedDatabaseId: dbRules.loadedDatabaseId,
      switchTick,
    }),
    [
      dbRules.rules,
      dbRules.formulaRules,
      dbRules.mutationPlotMeta,
      dbRules.mutationsLoaded,
      dbRules.isMutationsLoading,
      dbRules.loadedDatabaseId,
      switchTick,
    ],
  );

  return (
    <DatabaseRulesContext.Provider value={rulesValue}>
      {children}
    </DatabaseRulesContext.Provider>
  );
}

function MutationsFilterProvider({ children }) {
  const dbRules = useContext(DatabaseRulesContext);
  const filterState = useMutationTableFilter(dbRules);

  useEffect(() => {
    if (dbRules.switchTick > 0) {
      filterState.resetFilters();
    }
  }, [dbRules.switchTick, filterState.resetFilters]);

  const filterValue = useMemo(
    () => ({
      mutationFilter: filterState.mutationFilter,
      setMutationFilter: filterState.setMutationFilter,
      mutationSortColumn: filterState.mutationSortColumn,
      setMutationSortColumn: filterState.setMutationSortColumn,
      mutationSortAsc: filterState.mutationSortAsc,
      setMutationSortAsc: filterState.setMutationSortAsc,
      formulaFilter: filterState.formulaFilter,
      setFormulaFilter: filterState.setFormulaFilter,
      mutationColumns: filterState.mutationColumns,
      formulaColumns: filterState.formulaColumns,
      displayedRules: filterState.displayedRules,
      displayedFormulaRules: filterState.displayedFormulaRules,
      downloadMutationsAsTsv: filterState.downloadMutationsAsTsv,
      downloadFormulaRulesAsTsv: filterState.downloadFormulaRulesAsTsv,
    }),
    [
      filterState.mutationFilter,
      filterState.setMutationFilter,
      filterState.mutationSortColumn,
      filterState.setMutationSortColumn,
      filterState.mutationSortAsc,
      filterState.setMutationSortAsc,
      filterState.formulaFilter,
      filterState.setFormulaFilter,
      filterState.mutationColumns,
      filterState.formulaColumns,
      filterState.displayedRules,
      filterState.displayedFormulaRules,
      filterState.downloadMutationsAsTsv,
      filterState.downloadFormulaRulesAsTsv,
    ],
  );

  return (
    <MutationsFilterContext.Provider value={filterValue}>
      {children}
    </MutationsFilterContext.Provider>
  );
}

function ComparisonProvider({ children }) {
  const setStatusError = useContext(StatusErrorActionContext);
  const {
    sessionResults,
    selectedResultIndices,
    setSelectedResultIndices,
  } = useContext(SessionResultsTableContext);

  const comparison = useComparisonManager({
    sessionResults,
    selectedResultIndices,
    setSelectedResultIndices,
    setStatusError,
  });

  const comparisonValue = useMemo(
    () => comparison,
    [
      comparison.comparisonData,
      comparison.isComparisonBusy,
      comparison.nonSynonymousOnly,
      comparison.setNonSynonymousOnly,
      comparison.dbHitsOnly,
      comparison.setDbHitsOnly,
      comparison.comparisonDbId,
      comparison.comparisonRefName,
      comparison.selectAllComparable,
      comparison.fetchComparisonData,
      comparison.clearComparison,
    ],
  );

  return (
    <ComparisonContext.Provider value={comparisonValue}>
      {children}
    </ComparisonContext.Provider>
  );
}

function ShellProvider({ children }) {
  const { databases } = useContext(CoreDatabasesContext);
  const {
    selectedDatabase,
    selectedDatabaseId,
    setSelectedDatabaseId,
  } = useContext(CoreSelectedDbContext);
  const {
    legalLink,
    contactEmail,
    cliVersion,
    webVersion: shellWebVersion,
  } = useContext(CoreFooterContext);
  const { setActiveProfileMode, setAnalyzeSubMode } = useContext(CoreActionsContext);
  const { isUploading } = useContext(UploadBusyContext);
  const {
    isProfileBusy,
    isRegenerateBusy,
    runExampleProfile,
  } = useContext(ProfileBusyContext);
  const { batchSubmitting, isBatchUploading } = useContext(BatchBusyContext);

  const shellValue = useMemo(
    () => ({
      databases,
      selectedDatabase,
      selectedDatabaseId,
      setSelectedDatabaseId,
      legalLink,
      contactEmail,
      cliVersion,
      webVersion: shellWebVersion,
      setActiveProfileMode,
      setAnalyzeSubMode,
      runExampleProfile,
      isProfileBusy,
      isRegenerateBusy,
      isUploading,
      batchSubmitting,
      isBatchUploading,
    }),
    [
      databases,
      selectedDatabase,
      selectedDatabaseId,
      setSelectedDatabaseId,
      legalLink,
      contactEmail,
      cliVersion,
      shellWebVersion,
      setActiveProfileMode,
      setAnalyzeSubMode,
      runExampleProfile,
      isProfileBusy,
      isRegenerateBusy,
      isUploading,
      batchSubmitting,
      isBatchUploading,
    ],
  );

  return (
    <ShellContext.Provider value={shellValue}>
      {children}
    </ShellContext.Provider>
  );
}

export function DashboardProvider({ children }) {
  return (
    <StatusErrorProvider>
      <CoreShellProvider>
        <SessionProvider>
          <UploadProvider>
            <ProfileProvider>
              <BatchProvider>
                <DatabaseRulesProvider>
                  <MutationsFilterProvider>
                    <ComparisonProvider>
                      <ShellProvider>
                        {children}
                      </ShellProvider>
                    </ComparisonProvider>
                  </MutationsFilterProvider>
                </DatabaseRulesProvider>
              </BatchProvider>
            </ProfileProvider>
          </UploadProvider>
        </SessionProvider>
      </CoreShellProvider>
    </StatusErrorProvider>
  );
}

export function useCoreActions() {
  const ctx = useContext(CoreActionsContext);
  if (!ctx) {
    throw new Error('useCoreActions must be used within a DashboardProvider');
  }
  return ctx;
}

export function useOptionalShellLogic() {
  return useContext(ShellContext);
}

export function useOptionalUploadProgressStore() {
  return useContext(UploadProgressStoreContext);
}

export function useOptionalAnalyzePageLogic() {
  const analyzeMode = useContext(CoreAnalyzeModeContext);
  const uploadBusy = useContext(UploadBusyContext);
  const profileBusy = useContext(ProfileBusyContext);
  const batchHeader = useContext(BatchHeaderContext);
  const reportSelection = useContext(SessionReportSelectionContext);
  const sessionActions = useContext(SessionActionsContext);

  return useMemo(() => {
    if (
      !analyzeMode
      || !uploadBusy
      || !profileBusy
      || !batchHeader
      || !reportSelection
      || !sessionActions
    ) {
      return null;
    }
    return {
      isUploading: uploadBusy.isUploading,
      isBatchUploading: batchHeader.isBatchUploading,
      cancelUpload: profileBusy.cancelUpload,
      cancelBatchUpload: batchHeader.cancelBatchUpload,
      activeProfileMode: analyzeMode.activeProfileMode,
      setActiveProfileMode: analyzeMode.setActiveProfileMode,
      analyzeSubMode: analyzeMode.analyzeSubMode,
      setAnalyzeSubMode: analyzeMode.setAnalyzeSubMode,
      isProfileBusy: profileBusy.isProfileBusy,
      isRegenerateBusy: profileBusy.isRegenerateBusy,
      batchMode: batchHeader.batchMode,
      setBatchMode: batchHeader.setBatchMode,
      batchSubmitting: batchHeader.batchSubmitting,
      batchSubmitted: batchHeader.batchSubmitted,
      inlineReportPath: reportSelection.inlineReportPath,
      buildReportUrl: sessionActions.buildReportUrl,
      PROFILE_MODES: analyzeMode.PROFILE_MODES,
    };
  }, [analyzeMode, uploadBusy, profileBusy, batchHeader, reportSelection, sessionActions]);
}

export function useOptionalSingleAnalyzeLogic() {
  const selectedDb = useContext(CoreSelectedDbContext);
  const analyzeMode = useContext(CoreAnalyzeModeContext);
  const statusError = useContext(StatusErrorValueContext);
  const uploadBusy = useContext(UploadBusyContext);
  const reportSelection = useContext(SessionReportSelectionContext);
  const sessionActions = useContext(SessionActionsContext);
  const profile = useContext(ProfilePanelContext);

  return useMemo(() => {
    if (
      !selectedDb
      || !analyzeMode
      || statusError === null
      || !uploadBusy
      || !reportSelection
      || !sessionActions
      || !profile
    ) {
      return null;
    }
    return {
      selectedDatabase: selectedDb.selectedDatabase,
      activeProfileMode: analyzeMode.activeProfileMode,
      statusError,
      isUploading: uploadBusy.isUploading,
      selectedProfileReportPath: reportSelection.selectedProfileReportPath,
      setSelectedProfileReportPath: reportSelection.setSelectedProfileReportPath,
      inlineReportPath: reportSelection.inlineReportPath,
      reportOptions: reportSelection.reportOptions,
      buildReportUrl: sessionActions.buildReportUrl,
      buildArtifactUrl: sessionActions.buildArtifactUrl,
      ...profile,
    };
  }, [selectedDb, analyzeMode, statusError, uploadBusy, reportSelection, sessionActions, profile]);
}

export function useOptionalBatchAnalyzeLogic() {
  const selectedDb = useContext(CoreSelectedDbContext);
  const sessionActions = useContext(SessionActionsContext);
  const batch = useContext(BatchPanelContext);

  return useMemo(() => {
    if (!selectedDb || !sessionActions || !batch) {
      return null;
    }
    return {
      selectedDatabase: selectedDb.selectedDatabase,
      buildReportUrl: sessionActions.buildReportUrl,
      buildArtifactUrl: sessionActions.buildArtifactUrl,
      ...batch,
    };
  }, [selectedDb, sessionActions, batch]);
}

export function useOptionalMutationsLogic() {
  const dbRules = useContext(DatabaseRulesContext);
  const filterState = useContext(MutationsFilterContext);
  const statusError = useContext(StatusErrorValueContext);

  return useMemo(() => {
    if (!dbRules || !filterState || statusError === null) {
      return null;
    }
    return {
      rules: dbRules.rules,
      formulaRules: dbRules.formulaRules,
      mutationsLoaded: dbRules.mutationsLoaded,
      statusError,
      ...filterState,
    };
  }, [dbRules, filterState, statusError]);
}

export function useOptionalDatabasePageLogic() {
  const selectedDb = useContext(CoreSelectedDbContext);
  const dbRules = useContext(DatabaseRulesContext);

  return useMemo(() => {
    if (!selectedDb || !dbRules) {
      return null;
    }
    return {
      rules: dbRules.rules,
      formulaRules: dbRules.formulaRules,
      mutationPlotMeta: dbRules.mutationPlotMeta,
      selectedDatabase: selectedDb.selectedDatabase,
    };
  }, [selectedDb, dbRules]);
}

export function useOptionalResultsLogic() {
  const resultsTable = useContext(SessionResultsTableContext);
  const sessionActions = useContext(SessionActionsContext);
  const statusError = useContext(StatusErrorValueContext);
  const comparison = useContext(ComparisonContext);

  return useMemo(() => {
    if (!resultsTable || !sessionActions || statusError === null || !comparison) {
      return null;
    }
    return {
      ...resultsTable,
      buildReportUrl: sessionActions.buildReportUrl,
      buildArtifactUrl: sessionActions.buildArtifactUrl,
      statusError,
      ...comparison,
    };
  }, [resultsTable, sessionActions, statusError, comparison]);
}

export function useOptionalCompareLogic() {
  const databasesCtx = useContext(CoreDatabasesContext);
  return useMemo(() => {
    if (!databasesCtx) return null;
    return { databases: databasesCtx.databases };
  }, [databasesCtx]);
}

export function useOptionalHomeLogic() {
  const databasesCtx = useContext(CoreDatabasesContext);
  return useMemo(() => {
    if (!databasesCtx) return null;
    return {
      databases: databasesCtx.databases,
      setSelectedDatabaseId: databasesCtx.setSelectedDatabaseId,
    };
  }, [databasesCtx]);
}

export function useOptionalAboutLogic() {
  const footerCtx = useContext(CoreFooterContext);
  return useMemo(() => {
    if (!footerCtx) return null;
    return { contactEmail: footerCtx.contactEmail };
  }, [footerCtx]);
}
