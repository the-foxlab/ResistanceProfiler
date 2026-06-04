import { useState } from 'react';
import { FRONTEND_CONFIG } from '../config';
import { apiGet, apiPostRaw, apiUpload, formatUserError, formatPathStem, downloadArtifactBundle } from '../api';

export function useBatchManager({
  selectedDatabaseId,
  addReportPath,
  addUploadedPath,
  addResultArtifactPaths,
  setSessionResults,
  setUploadProgress,
  setStatusError,
}) {
  const [batchMode, setBatchMode] = useState('vcf');
  const [batchVcfFiles, setBatchVcfFiles] = useState([]);
  const [batchFastaFiles, setBatchFastaFiles] = useState([]);
  const [batchReferenceFasta, setBatchReferenceFastaState] = useState(null);
  const [batchSamples, setBatchSamples] = useState([]);
  const [batchSubmitting, setBatchSubmitting] = useState(false);
  const [isBatchDownloadBusy, setIsBatchDownloadBusy] = useState(false);
  const [batchError, setBatchError] = useState(null);
  const [batchRateLimitCooldown, setBatchRateLimitCooldown] = useState(0);
  const [batchSubmitted, setBatchSubmitted] = useState(false);
  const [batchMaxSamples, setBatchMaxSamples] = useState(25);
  const [sampleLimitPerMinute, setSampleLimitPerMinute] = useState(25);
  const [batchVcfCutoffs, setBatchVcfCutoffs] = useState({
    min_af: FRONTEND_CONFIG.profile.vcf.minAf,
    min_depth: FRONTEND_CONFIG.profile.vcf.minDepth,
  });

  const addBatchVcfFiles = async (files) => {
    const toUpload = Array.from(files).slice(0, batchMaxSamples - batchVcfFiles.length);
    for (const file of toUpload) {
      try {
        setUploadProgress({
          percent: 0,
          fileName: `BATCH VCF - ${file.name}`,
        });
        const response = await apiUpload('/api/upload/vcf', file, (percent) => {
          setUploadProgress((prev) => ({
            ...prev,
            percent,
          }));
        });
        setUploadProgress((prev) => ({ ...prev, percent: 100 }));
        setBatchVcfFiles((prev) => [...prev, { path: response.file_path, name: file.name, size: file.size }]);
        addUploadedPath(response.file_path);
      } catch (error) {
        setBatchError(formatUserError(error.message));
      }
    }
  };

  const addBatchFastaFiles = async (files) => {
    const toUpload = Array.from(files).slice(0, batchMaxSamples - batchFastaFiles.length);
    for (const file of toUpload) {
      try {
        setUploadProgress({
          percent: 0,
          fileName: `BATCH FASTA - ${file.name}`,
        });
        const response = await apiUpload('/api/upload/fasta', file, (percent) => {
          setUploadProgress((prev) => ({
            ...prev,
            percent,
          }));
        });
        setUploadProgress((prev) => ({ ...prev, percent: 100 }));
        setBatchFastaFiles((prev) => [...prev, { path: response.file_path, name: file.name, size: file.size }]);
        addUploadedPath(response.file_path);
      } catch (error) {
        setBatchError(formatUserError(error.message));
      }
    }
  };

  const removeBatchFile = (index) => {
    if (batchMode === 'vcf') {
      setBatchVcfFiles((prev) => prev.filter((_, i) => i !== index));
    } else {
      setBatchFastaFiles((prev) => prev.filter((_, i) => i !== index));
    }
  };

  const uploadBatchReferenceFasta = async (file) => {
    try {
      setUploadProgress({
        percent: 0,
        fileName: `BATCH REF - ${file.name}`,
      });
      const response = await apiUpload('/api/upload/fasta', file, (percent) => {
        setUploadProgress((prev) => ({
          ...prev,
          percent,
        }));
      });
      setUploadProgress((prev) => ({ ...prev, percent: 100 }));
      setBatchReferenceFastaState({ path: response.file_path, name: file.name });
      addUploadedPath(response.file_path);
    } catch (error) {
      setBatchError(formatUserError(error.message));
    }
  };

  const pollBatchJobs = async (initialSamples) => {
    const terminal = new Set(['succeeded', 'failed']);
    let samples = initialSamples.map((s) => ({ ...s }));
    for (;;) {
      const pending = samples.filter((s) => !terminal.has(s.status));
      if (pending.length === 0) break;
      await new Promise((resolve) => setTimeout(resolve, FRONTEND_CONFIG.profile.jobPollIntervalMs));
      const updated = await Promise.all(
        pending.map(async (sample) => {
          try {
            const payload = await apiGet(`/api/jobs/${sample.job_id}`);
            return {
              ...sample,
              status: payload.status,
              result: payload.result || null,
              errorMessage: payload.error ? formatUserError(payload.error) : null,
            };
          } catch (error) {
            return { ...sample, errorMessage: formatUserError(error.message) };
          }
        })
      );
      samples = samples.map((s) => updated.find((u) => u.job_id === s.job_id) || s);
      const succeededResults = samples
        .filter((sample) => sample.status === 'succeeded' && sample.result)
        .map((sample) => sample.result);
      succeededResults.forEach((result) => {
        addResultArtifactPaths(result);
      });
      if (succeededResults.length > 0) {
        setSessionResults((prev) => {
          const next = [...prev];
          succeededResults.forEach((result) => {
            const alreadyPresent = result.run_id
              ? next.some((existing) => existing.run_id === result.run_id)
              : next.some((existing) => existing.report_html_path === result.report_html_path);
            if (!alreadyPresent) {
              next.push(result);
            }
          });
          return next;
        });
      }
      setBatchSamples(
        samples.map((s) => ({
          job_id: s.job_id,
          sample_name: s.sample_name,
          status: s.status,
          errorMessage: s.errorMessage || null,
          report_url: s.result ? s.result.report_html_path : null,
          reportHtmlPath: s.result ? s.result.report_html_path : null,
          reportPdfPath: s.result ? s.result.report_pdf_path : null,
          reportJsonPath: s.result ? s.result.report_json_path : null,
        }))
      );
    }
  };

  const submitBatch = async () => {
    setBatchError(null);
    setBatchSubmitting(true);
    const files = batchMode === 'vcf' ? batchVcfFiles : batchFastaFiles;
    if (files.length === 0) {
      setBatchError('No files uploaded.');
      setBatchSubmitting(false);
      return;
    }
    try {
      let responseData;
      if (batchMode === 'vcf') {
        const sampleNames = batchVcfFiles.map((file) => formatPathStem(file.name));
        if (!Number.isFinite(batchVcfCutoffs.min_af) || batchVcfCutoffs.min_af < 0 || batchVcfCutoffs.min_af > 1) {
          throw new Error('Frequency cutoff (min AF) must be a number between 0 and 1.');
        }
        if (!Number.isInteger(batchVcfCutoffs.min_depth) || batchVcfCutoffs.min_depth < 0) {
          throw new Error('Coverage cutoff (min depth) must be an integer greater than or equal to 0.');
        }
        const body = {
          vcf_paths: batchVcfFiles.map((f) => f.path),
          sample_names: sampleNames,
          input_display_names: batchVcfFiles.map((f) => f.name),
          reference_fasta_path: batchReferenceFasta.path,
          db_path: selectedDatabaseId,
          min_af: batchVcfCutoffs.min_af,
          min_depth: batchVcfCutoffs.min_depth,
          threads: FRONTEND_CONFIG.profile.threads,
        };
        const response = await apiPostRaw('/api/profile/batch/vcf', body);
        if (response.status === 429) {
          setBatchRateLimitCooldown(60);
          setBatchError(`Rate limit reached. At most ${sampleLimitPerMinute} samples can be analyzed per minute.`);
          return;
        }
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(formatUserError(payload.detail || `Request failed: ${response.status}`));
        }
        responseData = await response.json();
      } else {
        const sampleNames = batchFastaFiles.map((file) => formatPathStem(file.name));
        const body = {
          fasta_paths: batchFastaFiles.map((f) => f.path),
          sample_names: sampleNames,
          input_display_names: batchFastaFiles.map((f) => f.name),
          db_path: selectedDatabaseId,
        };
        const response = await apiPostRaw('/api/profile/batch/fasta', body);
        if (response.status === 429) {
          setBatchRateLimitCooldown(60);
          setBatchError(`Rate limit reached. At most ${sampleLimitPerMinute} samples can be analyzed per minute.`);
          return;
        }
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(formatUserError(payload.detail || `Request failed: ${response.status}`));
        }
        responseData = await response.json();
      }
      const initialSamples = responseData.samples.map((s) => ({
        ...s,
        errorMessage: null,
        report_url: null,
        reportHtmlPath: null,
        reportPdfPath: null,
        reportJsonPath: null,
      }));
      setBatchSamples(initialSamples);
      setBatchSubmitted(true);
      await pollBatchJobs(initialSamples);
    } catch (error) {
      setBatchError(formatUserError(error.message));
    } finally {
      setBatchSubmitting(false);
    }
  };

  const resetBatch = () => {
    setBatchVcfFiles([]);
    setBatchFastaFiles([]);
    setBatchReferenceFastaState(null);
    setBatchSamples([]);
    setBatchSubmitting(false);
    setBatchError(null);
    setBatchRateLimitCooldown(0);
    setBatchSubmitted(false);
  };

  const downloadAllBatchArtifacts = async () => {
    const artifactPaths = batchSamples.flatMap((sample) =>
      [sample.reportHtmlPath, sample.reportPdfPath, sample.reportJsonPath].filter(Boolean)
    );
    if (artifactPaths.length === 0) {
      setBatchError('No completed batch artifacts are available for download.');
      return;
    }
    setBatchError(null);
    setIsBatchDownloadBusy(true);
    try {
      await downloadArtifactBundle(artifactPaths, 'respro-batch-artifacts.zip');
    } catch (error) {
      setBatchError(formatUserError(error.message));
    } finally {
      setIsBatchDownloadBusy(false);
    }
  };

  return {
    batchMode,
    setBatchMode,
    batchVcfFiles,
    batchFastaFiles,
    batchReferenceFasta,
    batchSamples,
    batchSubmitting,
    isBatchDownloadBusy,
    batchError,
    batchRateLimitCooldown,
    setBatchRateLimitCooldown,
    batchSubmitted,
    batchMaxSamples,
    setBatchMaxSamples,
    sampleLimitPerMinute,
    setSampleLimitPerMinute,
    batchVcfCutoffs,
    setBatchVcfCutoffs,
    addBatchVcfFiles,
    addBatchFastaFiles,
    removeBatchFile,
    uploadBatchReferenceFasta,
    submitBatch,
    downloadAllBatchArtifacts,
    resetBatch,
  };
}
