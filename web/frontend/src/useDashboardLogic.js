import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import faviconSrc from './assets/favicon.svg';
import packageJson from '../package.json';
import { API_BASE, apiGet, isAbortError, setArtifactBundleLimit } from './api';
import { sanitizeEmailAddress, sanitizeHttpUrl } from './utils';
import { PROFILE_MODES } from './constants';
import { useProfileSubmit } from './hooks/useProfileSubmit';
import { useBatchManager } from './hooks/useBatchManager';
import { useMutationBrowser } from './hooks/useMutationBrowser';
import { useSessionResults } from './hooks/useSessionResults';
import { useSessionCleanup } from './hooks/useSessionCleanup';
import { useUploadManager } from './hooks/useUploadManager';
import { useComparisonManager } from './hooks/useComparisonManager';

export const webVersion = packageJson.version;

export function useDashboardLogic() {
  const [databases, setDatabases] = useState([]);
  const [selectedDatabaseId, setSelectedDatabaseId] = useState('');
  const [statusError, setStatusError] = useState('');
  const [legalLink, setLegalLink] = useState(null);
  const [contactEmail, setContactEmail] = useState(null);
  const [cliVersion, setCliVersion] = useState(null);
  const [activeProfileMode, setActiveProfileMode] = useState('vcf');
  const [analyzeSubMode, setAnalyzeSubMode] = useState('single');
  const navigate = useNavigate();

  const selectedDatabase = databases.find((item) => item.id === selectedDatabaseId) || null;

  const session = useSessionResults();
  const upload = useUploadManager();
  const mutations = useMutationBrowser({ setStatusError });
  const profile = useProfileSubmit({
    setStatusError,
    addUploadedPath: session.addUploadedPath,
    addResultArtifactPaths: session.addResultArtifactPaths,
    setSessionResults: session.setSessionResults,
    setSelectedProfileReportPath: session.setSelectedProfileReportPath,
    setInlineReportPath: session.setInlineReportPath,
    databases,
    selectedDatabaseId,
    activeProfileMode,
    setUploadProgress: upload.setUploadProgress,
    beginUpload: upload.beginUpload,
    endUpload: upload.endUpload,
  });
  const batch = useBatchManager({
    selectedDatabaseId,
    addUploadedPath: session.addUploadedPath,
    addResultArtifactPaths: session.addResultArtifactPaths,
    setSessionResults: session.setSessionResults,
    setUploadProgress: upload.setUploadProgress,
  });
  const comparison = useComparisonManager({
    sessionResults: session.sessionResults,
    selectedResultIndices: session.selectedResultIndices,
    setSelectedResultIndices: session.setSelectedResultIndices,
    setStatusError,
  });

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
        if (Number.isFinite(uiConfig.batch_max_samples) && uiConfig.batch_max_samples > 0) {
          batch.setBatchMaxSamples(uiConfig.batch_max_samples);
        }
        if (Number.isFinite(uiConfig.sample_limit_per_minute) && uiConfig.sample_limit_per_minute > 0) {
          batch.setSampleLimitPerMinute(uiConfig.sample_limit_per_minute);
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
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    mutations.loadMutations(selectedDatabaseId, { signal: controller.signal });
    return () => controller.abort();
  }, [selectedDatabaseId, mutations.loadMutations]);

  useSessionCleanup(session.uploadedPaths, session.reportPaths);

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

  const { loadMutations: _loadMutations, ...mutationState } = mutations;
  const { setBatchMaxSamples: _setMax, setSampleLimitPerMinute: _setLimit, ...batchState } = batch;

  return {
    PROFILE_MODES,
    databases,
    selectedDatabase,
    selectedDatabaseId,
    setSelectedDatabaseId,
    statusError,
    legalLink,
    contactEmail,
    cliVersion,
    webVersion,
    navigate,
    activeProfileMode,
    setActiveProfileMode,
    analyzeSubMode,
    setAnalyzeSubMode,
    uploadProgress: upload.uploadProgress,
    isUploading: upload.isUploading,
    sessionResults: session.sessionResults,
    selectedProfileReportPath: session.selectedProfileReportPath,
    setSelectedProfileReportPath: session.setSelectedProfileReportPath,
    inlineReportPath: session.inlineReportPath,
    reportOptions: session.reportOptions,
    isSessionDownloadBusy: session.isSessionDownloadBusy,
    selectedResultIndices: session.selectedResultIndices,
    setSelectedResultIndices: session.setSelectedResultIndices,
    downloadSelectedArtifacts: () => session.downloadSelectedArtifacts(setStatusError),
    ...mutationState,
    ...profile,
    ...batchState,
    ...comparison,
  };
}

export function _resolveLegalLink(legalData) {
  if (!legalData || !legalData.enabled) return null;
  if (legalData.kind === 'url') {
    const rawUrl = legalData.url === null || legalData.url === undefined ? '' : String(legalData.url).trim();
    if (!rawUrl) return `${API_BASE}/legal`;
    return sanitizeHttpUrl(rawUrl);
  }
  return `${API_BASE}/legal`;
}

export function _resolveContactEmail(contactData) {
  if (!contactData || !contactData.enabled || !contactData.email) return null;
  return sanitizeEmailAddress(contactData.email);
}
