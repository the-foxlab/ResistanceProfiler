import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { downloadArtifactBundle, formatUserError, isAbortError } from '../api';
import { formatReportLabel, selectIndexedItems } from '../utils';

export function useSessionResults() {
  const [sessionResults, setSessionResults] = useState([]);
  const [uploadedPaths, setUploadedPaths] = useState([]);
  const [reportPaths, setReportPaths] = useState([]);
  const [selectedProfileReportPath, setSelectedProfileReportPath] = useState('');
  const [inlineReportPath, setInlineReportPath] = useState('');
  const [isSessionDownloadBusy, setIsSessionDownloadBusy] = useState(false);
  const [selectedResultIndices, setSelectedResultIndices] = useState(new Set());

  const isMountedRef = useRef(true);
  const isDownloadBusyRef = useRef(false);
  const downloadAbortControllerRef = useRef(null);
  const hadDownloadErrorRef = useRef(false);
  const sessionResultsRef = useRef(sessionResults);
  const selectedResultIndicesRef = useRef(selectedResultIndices);
  sessionResultsRef.current = sessionResults;
  selectedResultIndicesRef.current = selectedResultIndices;

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      isDownloadBusyRef.current = false;
      if (downloadAbortControllerRef.current) {
        downloadAbortControllerRef.current.abort();
        downloadAbortControllerRef.current = null;
      }
    };
  }, []);

  const addUploadedPath = useCallback((id) => {
    setUploadedPaths((prev) => (prev.includes(id) ? prev : [...prev, id]));
  }, []);

  const addReportPath = useCallback((id) => {
    setReportPaths((prev) => (prev.includes(id) ? prev : [...prev, id]));
  }, []);

  const addResultArtifactPaths = useCallback((result) => {
    (result.artifact_ids || []).forEach((artifactId) => {
      if (artifactId) addReportPath(artifactId);
    });
  }, [addReportPath]);

  const downloadSelectedArtifacts = useCallback(async (setStatusError) => {
    if (isDownloadBusyRef.current) return;
    const artifactIds = selectIndexedItems(
      sessionResultsRef.current,
      selectedResultIndicesRef.current,
    ).flatMap((result) => result.artifact_ids || []);
    if (artifactIds.length === 0) return;

    if (hadDownloadErrorRef.current) {
      hadDownloadErrorRef.current = false;
      setStatusError?.('');
    }

    const controller = new AbortController();
    downloadAbortControllerRef.current = controller;
    isDownloadBusyRef.current = true;
    setIsSessionDownloadBusy(true);
    try {
      await downloadArtifactBundle(artifactIds, 'respro-selected-artifacts.zip', {
        signal: controller.signal,
      });
    } catch (error) {
      if (
        !isMountedRef.current
        || controller.signal.aborted
        || isAbortError(error)
      ) {
        return;
      }
      hadDownloadErrorRef.current = true;
      if (setStatusError) {
        setStatusError(formatUserError(error?.message));
      }
    } finally {
      const isCurrentController = downloadAbortControllerRef.current === controller;
      if (isCurrentController) {
        downloadAbortControllerRef.current = null;
        isDownloadBusyRef.current = false;
      }
      if (isMountedRef.current && isCurrentController) {
        setIsSessionDownloadBusy(false);
      }
    }
  }, []);

  // Newest first for quick access in the report selector.
  const reportOptions = useMemo(
    () =>
      sessionResults
        .map((result) => ({
          path: result.report_html_path,
          jsonPath: result.report_json_path || '',
          pdfPath: result.report_pdf_path || '',
          tsvPath: result.report_tsv_path || '',
          label: formatReportLabel(result),
          mode: result.mode,
        }))
        .reverse(),
    [sessionResults],
  );

  useEffect(() => {
    if (selectedProfileReportPath) {
      setInlineReportPath(selectedProfileReportPath);
    }
  }, [selectedProfileReportPath]);

  return {
    sessionResults,
    setSessionResults,
    uploadedPaths,
    reportPaths,
    selectedProfileReportPath,
    setSelectedProfileReportPath,
    inlineReportPath,
    setInlineReportPath,
    isSessionDownloadBusy,
    selectedResultIndices,
    setSelectedResultIndices,
    addUploadedPath,
    addResultArtifactPaths,
    reportOptions,
    downloadSelectedArtifacts,
  };
}
