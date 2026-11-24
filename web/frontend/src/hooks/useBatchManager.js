import { useRef, useState } from 'react';
import { FRONTEND_CONFIG } from '../config';
import { apiPostRaw, apiUpload, downloadArtifactBundle, formatPathStem, formatUserError } from '../api';
import { validateVcfCutoffs } from '../utils';
import { pollBatchJobs } from './batchPolling';

export function useBatchManager({
  selectedDatabaseId,
  addUploadedPath,
  addResultArtifactPaths,
  setSessionResults,
  setUploadProgress,
}) {
  const [batchMode, setBatchMode] = useState('vcf');
  const [batchVcfFiles, setBatchVcfFiles] = useState([]);
  const [batchFastaFiles, setBatchFastaFiles] = useState([]);
  const [batchJsonFiles, setBatchJsonFiles] = useState([]);
  const [batchReferenceFasta, setBatchReferenceFastaState] = useState(null);
  const [batchSamples, setBatchSamples] = useState([]);
  const [batchSubmitting, setBatchSubmitting] = useState(false);
  const [isBatchDownloadBusy, setIsBatchDownloadBusy] = useState(false);
  const [batchError, setBatchError] = useState(null);
  const [batchRateLimitCooldown, setBatchRateLimitCooldown] = useState(0);
  const [batchSubmitted, setBatchSubmitted] = useState(false);
  const [batchMaxSamples, setBatchMaxSamples] = useState(25);
  const [sampleLimitPerMinute, setSampleLimitPerMinute] = useState(25);
  const [activeBatchUploads, setActiveBatchUploads] = useState(0);
  const [batchVcfCutoffs, setBatchVcfCutoffs] = useState({
    min_af: FRONTEND_CONFIG.profile.vcf.minAf,
    min_depth: FRONTEND_CONFIG.profile.vcf.minDepth,
  });

  const currentBatchUploadRef = useRef(null);
  const isBatchUploadCanceledRef = useRef(false);

  const cancelBatchUpload = () => {
    if (currentBatchUploadRef.current) {
      isBatchUploadCanceledRef.current = true;
      currentBatchUploadRef.current.abort();
    }
  };

  // Shared single-file upload runner with progress, cancel suppression, and active count.
  const uploadOneFile = async (endpoint, labelPrefix, file, onSuccess) => {
    setActiveBatchUploads((count) => count + 1);
    try {
      setUploadProgress({ percent: 0, fileName: `${labelPrefix} - ${file.name}` });
      const response = await apiUpload(
        endpoint,
        file,
        (percent) => setUploadProgress((prev) => ({ ...prev, percent })),
        (request) => {
          currentBatchUploadRef.current = request;
        },
      );
      setUploadProgress((prev) => ({ ...prev, percent: 100 }));
      addUploadedPath(response.upload_id);
      onSuccess(response, file);
    } catch (error) {
      if (!isBatchUploadCanceledRef.current) {
        setBatchError(formatUserError(error.message));
      }
    } finally {
      setActiveBatchUploads((count) => (count > 0 ? count - 1 : 0));
    }
  };

  const uploadBatchFileList = async (files, { endpoint, labelPrefix, limit = null, onUploaded }) => {
    isBatchUploadCanceledRef.current = false;
    const list = Array.from(files);
    const toUpload = limit === null ? list : list.slice(0, limit);
    for (const file of toUpload) {
      await uploadOneFile(endpoint, labelPrefix, file, onUploaded);
      if (isBatchUploadCanceledRef.current) break;
    }
    isBatchUploadCanceledRef.current = false;
  };

  const addBatchVcfFiles = (files) =>
    uploadBatchFileList(files, {
      endpoint: '/api/upload/vcf',
      labelPrefix: 'BATCH VCF',
      limit: batchMaxSamples - batchVcfFiles.length,
      onUploaded: (response, file) =>
        setBatchVcfFiles((prev) => [
          ...prev,
          { uploadId: response.upload_id, name: file.name, size: file.size, bamId: null, bamName: null, bamSize: null },
        ]),
    });

  const addBatchFastaFiles = (files) =>
    uploadBatchFileList(files, {
      endpoint: '/api/upload/fasta',
      labelPrefix: 'BATCH FASTA',
      limit: batchMaxSamples - batchFastaFiles.length,
      onUploaded: (response, file) =>
        setBatchFastaFiles((prev) => [...prev, { uploadId: response.upload_id, name: file.name, size: file.size }]),
    });

  const addBatchJsonFiles = (files) =>
    uploadBatchFileList(files, {
      endpoint: '/api/upload/json',
      labelPrefix: 'BATCH JSON',
      limit: batchMaxSamples - batchJsonFiles.length,
      onUploaded: (response, file) =>
        setBatchJsonFiles((prev) => [...prev, { uploadId: response.upload_id, name: file.name, size: file.size }]),
    });

  // Auto-pair uploaded BAMs to VCF rows by case-insensitive filename stem; report unmatched/collisions.
  const addBatchBamFiles = async (files) => {
    const paired = [];
    const unmatched = [];
    const collisions = [];
    // Local set tracks rows claimed synchronously within this multi-select batch.
    const claimedIndices = new Set();

    await uploadBatchFileList(files, {
      endpoint: '/api/upload/bam',
      labelPrefix: 'BATCH BAM',
      onUploaded: (response, file) => {
        const bamStem = formatPathStem(file.name).toLowerCase();
        const matchIndex = batchVcfFiles.findIndex(
          (entry) => formatPathStem(entry.name).toLowerCase() === bamStem,
        );
        if (matchIndex === -1) {
          unmatched.push(file.name);
          return;
        }
        if (batchVcfFiles[matchIndex].bamId || claimedIndices.has(matchIndex)) {
          collisions.push(file.name);
          return;
        }
        claimedIndices.add(matchIndex);
        setBatchVcfFiles((prev) =>
          prev.map((entry, i) =>
            i === matchIndex
              ? { ...entry, bamId: response.upload_id, bamName: file.name, bamSize: file.size }
              : entry,
          ),
        );
        paired.push(file.name);
      },
    });

    if (unmatched.length > 0 || collisions.length > 0) {
      const parts = [];
      if (unmatched.length > 0) {
        parts.push(`${unmatched.length} BAM(s) matched no VCF row: ${unmatched.join(', ')}`);
      }
      if (collisions.length > 0) {
        parts.push(`${collisions.length} BAM(s) already paired (kept existing): ${collisions.join(', ')}`);
      }
      setBatchError(`BAM pairing — ${parts.join('; ')}. Use the per-row BAM control to override.`);
    }
    return { paired, unmatched, collisions };
  };

  const attachBatchBam = async (vcfIndex, file) => {
    isBatchUploadCanceledRef.current = false;
    await uploadOneFile('/api/upload/bam', 'BATCH BAM', file, (response) => {
      setBatchVcfFiles((prev) =>
        prev.map((entry, i) =>
          i === vcfIndex
            ? { ...entry, bamId: response.upload_id, bamName: file.name, bamSize: file.size }
            : entry,
        ),
      );
    });
  };

  const uploadBatchReferenceFasta = async (file) => {
    isBatchUploadCanceledRef.current = false;
    await uploadOneFile('/api/upload/fasta', 'BATCH REF', file, (response) => {
      setBatchReferenceFastaState({ uploadId: response.upload_id, name: file.name });
    });
  };

  const removeBatchFile = (index) => {
    const setter = batchMode === 'vcf' ? setBatchVcfFiles : batchMode === 'fasta' ? setBatchFastaFiles : setBatchJsonFiles;
    setter((prev) => prev.filter((_, i) => i !== index));
  };

  const postBatchRequest = async (endpoint, body) => {
    const response = await apiPostRaw(endpoint, body);
    if (response.status === 429) {
      setBatchRateLimitCooldown(60);
      setBatchError(`Rate limit reached. At most ${sampleLimitPerMinute} samples can be analyzed per minute.`);
      return null;
    }
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(formatUserError(payload.detail || `Request failed: ${response.status}`));
    }
    return response.json();
  };

  const buildBatchRequest = () => {
    if (batchMode === 'vcf') {
      validateVcfCutoffs(batchVcfCutoffs.min_af, batchVcfCutoffs.min_depth);
      return [
        '/api/profile/batch/vcf',
        {
          vcf_ids: batchVcfFiles.map((f) => f.uploadId),
          sample_names: batchVcfFiles.map((f) => formatPathStem(f.name)),
          input_display_names: batchVcfFiles.map((f) => f.name),
          reference_id: batchReferenceFasta.uploadId,
          db_path: selectedDatabaseId,
          min_af: batchVcfCutoffs.min_af,
          min_depth: batchVcfCutoffs.min_depth,
          threads: FRONTEND_CONFIG.profile.threads,
          bam_ids: batchVcfFiles.map((f) => f.bamId ?? null),
        },
      ];
    }
    if (batchMode === 'fasta') {
      return [
        '/api/profile/batch/fasta',
        {
          fasta_ids: batchFastaFiles.map((f) => f.uploadId),
          sample_names: batchFastaFiles.map((f) => formatPathStem(f.name)),
          input_display_names: batchFastaFiles.map((f) => f.name),
          db_path: selectedDatabaseId,
        },
      ];
    }
    return [
      '/api/regenerate/batch',
      {
        json_ids: batchJsonFiles.map((f) => f.uploadId),
        sample_names: batchJsonFiles.map((f) => formatPathStem(f.name)),
        input_display_names: batchJsonFiles.map((f) => f.name),
        database_id: selectedDatabaseId,
      },
    ];
  };

  const recordBatchResults = (succeededResults) => {
    succeededResults.forEach((result) => addResultArtifactPaths(result));
    setSessionResults((prev) => {
      const next = [...prev];
      succeededResults.forEach((result) => {
        const exists = result.run_id
          ? next.some((item) => item.run_id === result.run_id)
          : next.some((item) => item.report_html_path === result.report_html_path);
        if (!exists) next.push(result);
      });
      return next;
    });
  };

  const submitBatch = async () => {
    setBatchError(null);
    setBatchSubmitting(true);
    const files = batchMode === 'vcf' ? batchVcfFiles : batchMode === 'fasta' ? batchFastaFiles : batchJsonFiles;
    if (files.length === 0) {
      setBatchError('No files uploaded.');
      setBatchSubmitting(false);
      return;
    }
    try {
      const [endpoint, body] = buildBatchRequest();
      const responseData = await postBatchRequest(endpoint, body);
      if (!responseData) return;

      const initialSamples = responseData.samples.map((sample) => ({
        ...sample,
        errorMessage: null,
        report_url: null,
        reportHtmlPath: null,
        reportPdfPath: null,
        reportJsonPath: null,
        reportTsvPath: null,
        artifactIds: null,
      }));
      setBatchSamples(initialSamples);
      setBatchSubmitted(true);
      setBatchVcfFiles([]);
      setBatchFastaFiles([]);
      setBatchJsonFiles([]);
      setBatchReferenceFastaState(null);
      await pollBatchJobs({
        initialSamples,
        onSamplesChange: setBatchSamples,
        onSucceededResults: recordBatchResults,
      });
    } catch (error) {
      setBatchError(formatUserError(error.message));
    } finally {
      setBatchSubmitting(false);
    }
  };

  const resetBatch = () => {
    setBatchVcfFiles([]);
    setBatchFastaFiles([]);
    setBatchJsonFiles([]);
    setBatchReferenceFastaState(null);
    setBatchSamples([]);
    setBatchSubmitting(false);
    setBatchError(null);
    setBatchRateLimitCooldown(0);
    setBatchSubmitted(false);
  };

  const downloadAllBatchArtifacts = async () => {
    const artifactIds = batchSamples.flatMap((sample) => sample.artifactIds || []);
    if (artifactIds.length === 0) {
      setBatchError('No completed batch artifacts are available for download.');
      return;
    }
    setBatchError(null);
    setIsBatchDownloadBusy(true);
    try {
      await downloadArtifactBundle(artifactIds, 'respro-batch-artifacts.zip');
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
    batchJsonFiles,
    batchReferenceFasta,
    batchSamples,
    isBatchUploading: activeBatchUploads > 0,
    batchSubmitting,
    isBatchDownloadBusy,
    batchError,
    batchRateLimitCooldown,
    setBatchRateLimitCooldown,
    batchSubmitted,
    batchMaxSamples,
    setBatchMaxSamples,
    setSampleLimitPerMinute,
    batchVcfCutoffs,
    setBatchVcfCutoffs,
    addBatchVcfFiles,
    addBatchFastaFiles,
    addBatchJsonFiles,
    addBatchBamFiles,
    attachBatchBam,
    removeBatchFile,
    uploadBatchReferenceFasta,
    submitBatch,
    cancelBatchUpload,
    downloadAllBatchArtifacts,
    resetBatch,
  };
}
