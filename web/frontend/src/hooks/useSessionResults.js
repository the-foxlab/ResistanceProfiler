import { useMemo, useState } from 'react';
import { downloadArtifactBundle, formatResultTimestamp, formatUserError } from '../api';

export function useSessionResults() {
  const [sessionResults, setSessionResults] = useState([]);
  const [uploadedPaths, setUploadedPaths] = useState([]);
  const [reportPaths, setReportPaths] = useState([]);
  const [selectedProfileReportPath, setSelectedProfileReportPath] = useState('');
  const [inlineReportPath, setInlineReportPath] = useState('');
  const [inlineReportLabel, setInlineReportLabel] = useState('');
  const [isSessionDownloadBusy, setIsSessionDownloadBusy] = useState(false);
  const [selectedResultIndices, setSelectedResultIndices] = useState(new Set());

  const addUploadedPath = (path) => {
    // Track uploaded files so they can be cleaned up when the page closes.
    setUploadedPaths((prev) => {
      if (prev.includes(path)) {
        return prev;
      }
      return [...prev, path];
    });
  };

  const addReportPath = (path) => {
    // Track generated reports for the same cleanup endpoint.
    setReportPaths((prev) => {
      if (prev.includes(path)) {
        return prev;
      }
      return [...prev, path];
    });
  };

  const addResultArtifactPaths = (result) => {
    [
      result.report_html_path,
      result.report_json_path,
      result.report_pdf_path,
    ].forEach((path) => {
      if (path) {
        addReportPath(path);
      }
    });
  };

  const downloadAllSessionArtifacts = async (setStatusError) => {
    const artifactPaths = sessionResults.flatMap((result) =>
      [result.report_html_path, result.report_pdf_path, result.report_json_path].filter(Boolean)
    );
    if (artifactPaths.length === 0) return;
    setIsSessionDownloadBusy(true);
    try {
      await downloadArtifactBundle(artifactPaths, 'respro-session-artifacts.zip');
    } catch (error) {
      if (setStatusError) {
        setStatusError(formatUserError(error.message));
      }
    } finally {
      setIsSessionDownloadBusy(false);
    }
  };

  const downloadSelectedArtifacts = async (setStatusError) => {
    const selectedResults = [...selectedResultIndices]
      .map((i) => sessionResults[i])
      .filter(Boolean);
    const artifactPaths = selectedResults.flatMap((r) =>
      [r.report_html_path, r.report_pdf_path, r.report_json_path].filter(Boolean)
    );
    if (artifactPaths.length === 0) return;
    try {
      await downloadArtifactBundle(artifactPaths, 'respro-selected-artifacts.zip');
    } catch (error) {
      if (setStatusError) {
        setStatusError(formatUserError(error.message));
      }
    }
  };

  // Reports are shown newest first for quick access after job completion.
  const reportOptions = useMemo(() => {
    return sessionResults
      .map((result) => ({
        path: result.report_html_path,
        jsonPath: result.report_json_path || '',
        pdfPath: result.report_pdf_path || '',
        label: `${result.sample_name} (${result.reference_name}) - ${formatResultTimestamp(result.created_at)}`,
        mode: result.mode,
      }))
      .reverse();
  }, [sessionResults]);

  return {
    sessionResults,
    setSessionResults,
    uploadedPaths,
    reportPaths,
    selectedProfileReportPath,
    setSelectedProfileReportPath,
    inlineReportPath,
    setInlineReportPath,
    inlineReportLabel,
    setInlineReportLabel,
    isSessionDownloadBusy,
    selectedResultIndices,
    setSelectedResultIndices,
    addUploadedPath,
    addReportPath,
    addResultArtifactPaths,
    reportOptions,
    downloadAllSessionArtifacts,
    downloadSelectedArtifacts,
  };
}
