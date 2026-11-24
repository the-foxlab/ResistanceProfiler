import { useEffect, useMemo, useState } from 'react';
import { downloadArtifactBundle, formatUserError } from '../api';
import { formatReportLabel, selectIndexedItems } from '../utils';

export function useSessionResults() {
  const [sessionResults, setSessionResults] = useState([]);
  const [uploadedPaths, setUploadedPaths] = useState([]);
  const [reportPaths, setReportPaths] = useState([]);
  const [selectedProfileReportPath, setSelectedProfileReportPath] = useState('');
  const [inlineReportPath, setInlineReportPath] = useState('');
  const [isSessionDownloadBusy] = useState(false);
  const [selectedResultIndices, setSelectedResultIndices] = useState(new Set());

  const addUnique = (setter) => (id) =>
    setter((prev) => (prev.includes(id) ? prev : [...prev, id]));

  const addUploadedPath = addUnique(setUploadedPaths);
  const addReportPath = addUnique(setReportPaths);

  const addResultArtifactPaths = (result) => {
    (result.artifact_ids || []).forEach((artifactId) => {
      if (artifactId) addReportPath(artifactId);
    });
  };

  const downloadSelectedArtifacts = async (setStatusError) => {
    const artifactIds = selectIndexedItems(sessionResults, selectedResultIndices)
      .flatMap((result) => result.artifact_ids || []);
    if (artifactIds.length === 0) return;
    try {
      await downloadArtifactBundle(artifactIds, 'respro-selected-artifacts.zip');
    } catch (error) {
      if (setStatusError) setStatusError(formatUserError(error.message));
    }
  };

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
