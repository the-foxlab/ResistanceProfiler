import { useCallback, useEffect, useRef, useState } from 'react';
import { FRONTEND_CONFIG } from '../config';
import {
  ApiError,
  apiPostRaw,
  apiUpload,
  downloadArtifactBundle,
  formatPathStem,
  formatUserError,
  isAbortError,
  parseRetryAfter,
} from '../api';
import { validateVcfCutoffs } from '../utils';
import { pollBatchJobs } from './batchPolling';

export function useBatchManager({
  selectedDatabaseId,
  addUploadedPath,
  addResultArtifactPaths,
  setSessionResults,
  setUploadProgress,
}) {
  const [batchMode, setBatchModeState] = useState('vcf');
  const [batchVcfFiles, setBatchVcfFiles] = useState([]);
  const [batchFastaFiles, setBatchFastaFiles] = useState([]);
  const [batchJsonFiles, setBatchJsonFiles] = useState([]);
  const [batchReferenceFasta, setBatchReferenceFastaState] = useState(null);
  const [batchSamples, setBatchSamples] = useState([]);
  const [batchSubmitting, setBatchSubmitting] = useState(false);
  const [isBatchPolling, setIsBatchPolling] = useState(false);
  const [isBatchDownloadBusy, setIsBatchDownloadBusy] = useState(false);
  const [batchError, setBatchError] = useState(null);
  const [batchRateLimitCooldown, setBatchRateLimitCooldown] = useState(0);
  const [batchSubmitted, setBatchSubmitted] = useState(false);
  const [batchMaxSamples, setBatchMaxSamplesState] = useState(25);
  const [sampleLimitPerMinute, setSampleLimitPerMinute] = useState(25);
  const [activeBatchUploads, setActiveBatchUploads] = useState(0);
  const [batchVcfCutoffs, setBatchVcfCutoffs] = useState({
    min_af: FRONTEND_CONFIG.profile.vcf.minAf,
    min_depth: FRONTEND_CONFIG.profile.vcf.minDepth,
  });

  const generationRef = useRef(0);
  const nextRowIdRef = useRef(0);
  const activeUploadHandlesRef = useRef(new Set());
  const activeUploadOpsRef = useRef(new Set());
  const refUploadTokenRef = useRef(0);
  const rowBamUploadTokenRef = useRef(new Map());
  const pollAbortControllerRef = useRef(null);
  const isMountedRef = useRef(true);
  const isDownloadBusyRef = useRef(false);
  const downloadAbortControllerRef = useRef(null);

  const batchModeRef = useRef(batchMode);
  const batchVcfFilesRef = useRef(batchVcfFiles);
  const batchFastaFilesRef = useRef(batchFastaFiles);
  const batchJsonFilesRef = useRef(batchJsonFiles);
  const batchReferenceFastaRef = useRef(batchReferenceFasta);
  const batchMaxSamplesRef = useRef(batchMaxSamples);

  useEffect(() => {
    batchModeRef.current = batchMode;
  }, [batchMode]);
  useEffect(() => {
    batchVcfFilesRef.current = batchVcfFiles;
  }, [batchVcfFiles]);
  useEffect(() => {
    batchFastaFilesRef.current = batchFastaFiles;
  }, [batchFastaFiles]);
  useEffect(() => {
    batchJsonFilesRef.current = batchJsonFiles;
  }, [batchJsonFiles]);
  useEffect(() => {
    batchReferenceFastaRef.current = batchReferenceFasta;
  }, [batchReferenceFasta]);
  useEffect(() => {
    batchMaxSamplesRef.current = batchMaxSamples;
  }, [batchMaxSamples]);

  const createRowId = () => `batch-row-${++nextRowIdRef.current}`;

  const updateBatchVcfFiles = useCallback((updater) => {
    batchVcfFilesRef.current = updater(batchVcfFilesRef.current);
    setBatchVcfFiles(updater);
  }, []);

  const updateBatchFastaFiles = useCallback((updater) => {
    batchFastaFilesRef.current = updater(batchFastaFilesRef.current);
    setBatchFastaFiles(updater);
  }, []);

  const updateBatchJsonFiles = useCallback((updater) => {
    batchJsonFilesRef.current = updater(batchJsonFilesRef.current);
    setBatchJsonFiles(updater);
  }, []);

  const updateBatchReferenceFasta = useCallback((nextValue) => {
    batchReferenceFastaRef.current = nextValue;
    setBatchReferenceFastaState(nextValue);
  }, []);

  const setBatchMaxSamples = useCallback((nextOrUpdater) => {
    if (typeof nextOrUpdater === 'function') {
      batchMaxSamplesRef.current = nextOrUpdater(batchMaxSamplesRef.current);
      setBatchMaxSamplesState(nextOrUpdater);
      return;
    }
    batchMaxSamplesRef.current = nextOrUpdater;
    setBatchMaxSamplesState(nextOrUpdater);
  }, []);

  const abortAllInFlightOps = useCallback(() => {
    generationRef.current += 1;
    for (const opState of activeUploadOpsRef.current) {
      opState.canceled = true;
    }
    for (const handle of activeUploadHandlesRef.current) {
      handle.canceled = true;
      handle.request?.abort?.();
    }
    if (pollAbortControllerRef.current) {
      pollAbortControllerRef.current.abort();
      pollAbortControllerRef.current = null;
    }
    isDownloadBusyRef.current = false;
    if (downloadAbortControllerRef.current) {
      downloadAbortControllerRef.current.abort();
      downloadAbortControllerRef.current = null;
    }
    refUploadTokenRef.current += 1;
    rowBamUploadTokenRef.current.clear();
  }, []);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      abortAllInFlightOps();
    };
  }, [abortAllInFlightOps]);

  const hasCooldown = batchRateLimitCooldown > 0;
  useEffect(() => {
    if (!hasCooldown) return undefined;
    const timer = setInterval(() => {
      setBatchRateLimitCooldown((prev) => (prev <= 1 ? 0 : prev - 1));
    }, 1000);
    return () => clearInterval(timer);
  }, [hasCooldown]);

  const cancelBatchUpload = useCallback(() => {
    for (const opState of activeUploadOpsRef.current) {
      opState.canceled = true;
    }
    for (const handle of activeUploadHandlesRef.current) {
      handle.canceled = true;
      handle.request?.abort?.();
    }
  }, []);

  // Shared single-file upload runner with per-upload cancel handle, generation guard, and cleanup.
  const uploadOneFile = async (endpoint, labelPrefix, file, onSuccess, { opState = null } = {}) => {
    if (opState?.canceled) return false;
    const opGeneration = generationRef.current;
    const handle = { request: null, canceled: false, opGeneration };
    activeUploadHandlesRef.current.add(handle);
    setActiveBatchUploads((count) => count + 1);
    try {
      setUploadProgress({ percent: 0, fileName: `${labelPrefix} - ${file.name}` });
      const response = await apiUpload(
        endpoint,
        file,
        (percent) => {
          if (!handle.canceled && !opState?.canceled && opGeneration === generationRef.current) {
            setUploadProgress((prev) => ({ ...prev, percent }));
          }
        },
        (request) => {
          handle.request = request;
          if (handle.canceled || opState?.canceled || opGeneration !== generationRef.current) {
            request.abort();
          }
        },
      );
      addUploadedPath(response.upload_id);
      if (handle.canceled || opState?.canceled || opGeneration !== generationRef.current) {
        return false;
      }
      setUploadProgress((prev) => ({ ...prev, percent: 100 }));
      onSuccess(response, file);
      return true;
    } catch (error) {
      if (
        !handle.canceled
        && !opState?.canceled
        && !isAbortError(error)
        && opGeneration === generationRef.current
      ) {
        setBatchError(formatUserError(error.message));
      }
      return false;
    } finally {
      handle.request = null;
      activeUploadHandlesRef.current.delete(handle);
      if (opGeneration === generationRef.current) {
        setActiveBatchUploads((count) => (count > 0 ? count - 1 : 0));
      }
    }
  };

  const uploadBatchFileList = async (files, { endpoint, labelPrefix, getRemainingCapacity = null, onUploaded }) => {
    const opGeneration = generationRef.current;
    const list = Array.from(files || []);
    if (list.length === 0) return;
    if (getRemainingCapacity && getRemainingCapacity() <= 0) {
      return;
    }
    const opState = { canceled: false, opGeneration };
    activeUploadOpsRef.current.add(opState);
    try {
      for (const file of list) {
        if (opState.canceled || opGeneration !== generationRef.current) break;
        if (getRemainingCapacity && getRemainingCapacity() <= 0) break;
        await uploadOneFile(endpoint, labelPrefix, file, onUploaded, { opState });
        if (opState.canceled || opGeneration !== generationRef.current) break;
      }
    } finally {
      activeUploadOpsRef.current.delete(opState);
    }
  };

  const addBatchVcfFiles = (files) =>
    uploadBatchFileList(files, {
      endpoint: '/api/upload/vcf',
      labelPrefix: 'BATCH VCF',
      getRemainingCapacity: () => Math.max(0, batchMaxSamplesRef.current - batchVcfFilesRef.current.length),
      onUploaded: (response, file) => {
        const row = {
          rowId: createRowId(),
          uploadId: response.upload_id,
          name: file.name,
          size: file.size,
          bamId: null,
          bamName: null,
          bamSize: null,
        };
        updateBatchVcfFiles((prev) => (prev.length >= batchMaxSamplesRef.current ? prev : [...prev, row]));
      },
    });

  const addBatchFastaFiles = (files) =>
    uploadBatchFileList(files, {
      endpoint: '/api/upload/fasta',
      labelPrefix: 'BATCH FASTA',
      getRemainingCapacity: () => Math.max(0, batchMaxSamplesRef.current - batchFastaFilesRef.current.length),
      onUploaded: (response, file) => {
        const row = {
          rowId: createRowId(),
          uploadId: response.upload_id,
          name: file.name,
          size: file.size,
        };
        updateBatchFastaFiles((prev) => (prev.length >= batchMaxSamplesRef.current ? prev : [...prev, row]));
      },
    });

  const addBatchJsonFiles = (files) =>
    uploadBatchFileList(files, {
      endpoint: '/api/upload/json',
      labelPrefix: 'BATCH JSON',
      getRemainingCapacity: () => Math.max(0, batchMaxSamplesRef.current - batchJsonFilesRef.current.length),
      onUploaded: (response, file) => {
        const row = {
          rowId: createRowId(),
          uploadId: response.upload_id,
          name: file.name,
          size: file.size,
        };
        updateBatchJsonFiles((prev) => (prev.length >= batchMaxSamplesRef.current ? prev : [...prev, row]));
      },
    });

  // Auto-pair uploaded BAMs to VCF rows by immutable rowId and case-insensitive filename stem.
  const addBatchBamFiles = async (files) => {
    const opGeneration = generationRef.current;
    const paired = [];
    const unmatched = [];
    const collisions = [];
    const claimedRowIds = new Set();

    await uploadBatchFileList(files, {
      endpoint: '/api/upload/bam',
      labelPrefix: 'BATCH BAM',
      onUploaded: (response, file) => {
        const bamStem = formatPathStem(file.name).toLowerCase();
        const targetRow = batchVcfFilesRef.current.find(
          (entry) => formatPathStem(entry.name).toLowerCase() === bamStem,
        );
        if (!targetRow) {
          unmatched.push(file.name);
          return;
        }
        if (targetRow.bamId || claimedRowIds.has(targetRow.rowId)) {
          collisions.push(file.name);
          return;
        }
        claimedRowIds.add(targetRow.rowId);
        updateBatchVcfFiles((prev) =>
          prev.map((entry) =>
            entry.rowId === targetRow.rowId && !entry.bamId
              ? { ...entry, bamId: response.upload_id, bamName: file.name, bamSize: file.size }
              : entry,
          ),
        );
        paired.push(file.name);
      },
    });

    if (opGeneration !== generationRef.current) {
      return { paired, unmatched, collisions };
    }

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

  const attachBatchBam = async (rowId, file) => {
    const opGeneration = generationRef.current;
    const token = (rowBamUploadTokenRef.current.get(rowId) || 0) + 1;
    rowBamUploadTokenRef.current.set(rowId, token);

    await uploadOneFile('/api/upload/bam', 'BATCH BAM', file, (response) => {
      if (opGeneration !== generationRef.current || rowBamUploadTokenRef.current.get(rowId) !== token) {
        return;
      }
      updateBatchVcfFiles((prev) =>
        prev.map((entry) =>
          entry.rowId === rowId
            ? { ...entry, bamId: response.upload_id, bamName: file.name, bamSize: file.size }
            : entry,
        ),
      );
    });
  };

  const uploadBatchReferenceFasta = async (file) => {
    const opGeneration = generationRef.current;
    const token = ++refUploadTokenRef.current;
    await uploadOneFile('/api/upload/fasta', 'BATCH REF', file, (response) => {
      if (opGeneration !== generationRef.current || token !== refUploadTokenRef.current) {
        return;
      }
      updateBatchReferenceFasta({ uploadId: response.upload_id, name: file.name });
    });
  };

  const removeBatchFile = useCallback((rowId) => {
    const mode = batchModeRef.current;
    const updater = mode === 'vcf'
      ? updateBatchVcfFiles
      : mode === 'fasta'
        ? updateBatchFastaFiles
        : updateBatchJsonFiles;
    updater((prev) => prev.filter((entry) => entry.rowId !== rowId));
  }, [updateBatchVcfFiles, updateBatchFastaFiles, updateBatchJsonFiles]);

  const setBatchMode = useCallback((nextMode) => {
    if (nextMode === batchModeRef.current) {
      return;
    }
    abortAllInFlightOps();
    batchModeRef.current = nextMode;
    setBatchModeState(nextMode);
    setActiveBatchUploads(0);
    setBatchSubmitting(false);
    setIsBatchPolling(false);
    setIsBatchDownloadBusy(false);
    setBatchError(null);
  }, [abortAllInFlightOps]);

  const postBatchRequest = async (endpoint, body, { signal, opGeneration } = {}) => {
    const response = await apiPostRaw(
      endpoint,
      body,
      signal !== undefined ? { signal } : undefined,
    );
    if (signal?.aborted || (opGeneration !== undefined && opGeneration !== generationRef.current)) {
      return null;
    }
    if (response.status === 429) {
      const rawRetryAfter = typeof response.headers?.get === 'function'
        ? response.headers.get('Retry-After')
        : response.headers?.['Retry-After'] ?? response.headers?.['retry-after'];
      const headerRetryAfter = parseRetryAfter(rawRetryAfter);
      const defaultCooldown = FRONTEND_CONFIG.batch?.defaultSubmitCooldownSeconds ?? 60;
      const cooldownSeconds = headerRetryAfter && headerRetryAfter > 0 ? headerRetryAfter : defaultCooldown;
      setBatchRateLimitCooldown(cooldownSeconds);
      setBatchError(`Rate limit reached. At most ${sampleLimitPerMinute} samples can be analyzed per minute.`);
      return null;
    }
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const detail = payload && typeof payload === 'object' ? payload.detail : undefined;
      throw new ApiError(formatUserError(detail || `Request failed: ${response.status}`), {
        status: response.status,
      });
    }
    return response.json();
  };

  const buildBatchRequest = (mode) => {
    if (mode === 'vcf') {
      const refFasta = batchReferenceFastaRef.current;
      if (!refFasta?.uploadId) {
        throw new Error('Please upload a shared reference FASTA file.');
      }
      validateVcfCutoffs(batchVcfCutoffs.min_af, batchVcfCutoffs.min_depth);
      const vcfFiles = batchVcfFilesRef.current;
      return [
        '/api/profile/batch/vcf',
        {
          vcf_ids: vcfFiles.map((f) => f.uploadId),
          sample_names: vcfFiles.map((f) => formatPathStem(f.name)),
          input_display_names: vcfFiles.map((f) => f.name),
          reference_id: refFasta.uploadId,
          db_path: selectedDatabaseId,
          min_af: batchVcfCutoffs.min_af,
          min_depth: batchVcfCutoffs.min_depth,
          threads: FRONTEND_CONFIG.profile.threads,
          bam_ids: vcfFiles.map((f) => f.bamId ?? null),
        },
      ];
    }
    if (mode === 'fasta') {
      const fastaFiles = batchFastaFilesRef.current;
      return [
        '/api/profile/batch/fasta',
        {
          fasta_ids: fastaFiles.map((f) => f.uploadId),
          sample_names: fastaFiles.map((f) => formatPathStem(f.name)),
          input_display_names: fastaFiles.map((f) => f.name),
          db_path: selectedDatabaseId,
        },
      ];
    }
    const jsonFiles = batchJsonFilesRef.current;
    return [
      '/api/regenerate/batch',
      {
        json_ids: jsonFiles.map((f) => f.uploadId),
        sample_names: jsonFiles.map((f) => formatPathStem(f.name)),
        input_display_names: jsonFiles.map((f) => f.name),
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
    const mode = batchModeRef.current;
    const files = mode === 'vcf'
      ? batchVcfFilesRef.current
      : mode === 'fasta'
        ? batchFastaFilesRef.current
        : batchJsonFilesRef.current;

    setBatchError(null);
    if (files.length === 0) {
      setBatchError('No files uploaded.');
      setBatchSubmitting(false);
      return;
    }

    const opGeneration = generationRef.current;
    if (pollAbortControllerRef.current) {
      pollAbortControllerRef.current.abort();
    }
    const controller = new AbortController();
    pollAbortControllerRef.current = controller;

    setBatchSubmitting(true);
    try {
      const [endpoint, body] = buildBatchRequest(mode);
      const responseData = await postBatchRequest(endpoint, body, {
        signal: controller.signal,
        opGeneration,
      });
      if (!responseData || opGeneration !== generationRef.current || controller.signal.aborted) {
        return;
      }

      const initialSamples = responseData.samples.map((sample) => ({
        ...sample,
        errorMessage: null,
        reportHtmlPath: null,
        reportPdfPath: null,
        reportJsonPath: null,
        reportTsvPath: null,
        artifactIds: null,
      }));
      setBatchSamples(initialSamples);
      setBatchSubmitted(true);
      updateBatchVcfFiles(() => []);
      updateBatchFastaFiles(() => []);
      updateBatchJsonFiles(() => []);
      updateBatchReferenceFasta(null);
      setIsBatchPolling(true);

      await pollBatchJobs({
        initialSamples,
        signal: controller.signal,
        onSamplesChange: (nextSamples) => {
          if (opGeneration === generationRef.current) {
            setBatchSamples(nextSamples);
          }
        },
        onSucceededResults: (results) => {
          if (opGeneration === generationRef.current) {
            recordBatchResults(results);
          }
        },
        onRateLimit: (seconds) => {
          if (opGeneration === generationRef.current) {
            setBatchRateLimitCooldown(seconds);
          }
        },
        onError: (message) => {
          if (opGeneration === generationRef.current) {
            setBatchError(message);
          }
        },
      });
    } catch (error) {
      if (!isAbortError(error) && !controller.signal.aborted && opGeneration === generationRef.current) {
        setBatchError(formatUserError(error.message));
      }
    } finally {
      const isCurrentController = pollAbortControllerRef.current === controller;
      if (isCurrentController) {
        pollAbortControllerRef.current = null;
      }
      if (isCurrentController && opGeneration === generationRef.current) {
        setIsBatchPolling(false);
        setBatchSubmitting(false);
      }
    }
  };

  const stopBatchPolling = useCallback(() => {
    generationRef.current += 1;
    if (pollAbortControllerRef.current) {
      pollAbortControllerRef.current.abort();
      pollAbortControllerRef.current = null;
    }
    setIsBatchPolling(false);
    setBatchSubmitting(false);
    setBatchRateLimitCooldown(0);
  }, []);

  const resetBatch = useCallback(() => {
    abortAllInFlightOps();
    updateBatchVcfFiles(() => []);
    updateBatchFastaFiles(() => []);
    updateBatchJsonFiles(() => []);
    updateBatchReferenceFasta(null);
    setBatchSamples([]);
    setActiveBatchUploads(0);
    setBatchSubmitting(false);
    setIsBatchPolling(false);
    setIsBatchDownloadBusy(false);
    setBatchError(null);
    setBatchRateLimitCooldown(0);
    setBatchSubmitted(false);
  }, [abortAllInFlightOps, updateBatchVcfFiles, updateBatchFastaFiles, updateBatchJsonFiles, updateBatchReferenceFasta]);

  const downloadAllBatchArtifacts = async () => {
    if (isDownloadBusyRef.current) {
      return;
    }
    const artifactIds = batchSamples.flatMap((sample) => sample.artifactIds || []);
    if (artifactIds.length === 0) {
      setBatchError('No completed batch artifacts are available for download.');
      return;
    }
    const opGeneration = generationRef.current;
    const controller = new AbortController();
    downloadAbortControllerRef.current = controller;
    isDownloadBusyRef.current = true;
    setBatchError(null);
    setIsBatchDownloadBusy(true);
    try {
      await downloadArtifactBundle(artifactIds, 'respro-batch-artifacts.zip', {
        signal: controller.signal,
      });
    } catch (error) {
      if (
        !isMountedRef.current
        || controller.signal.aborted
        || isAbortError(error)
        || opGeneration !== generationRef.current
      ) {
        return;
      }
      setBatchError(formatUserError(error?.message));
    } finally {
      const isCurrentController = downloadAbortControllerRef.current === controller;
      if (isCurrentController) {
        downloadAbortControllerRef.current = null;
        isDownloadBusyRef.current = false;
      }
      if (isMountedRef.current && isCurrentController && opGeneration === generationRef.current) {
        setIsBatchDownloadBusy(false);
      }
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
    isBatchPolling,
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
    stopBatchPolling,
    cancelBatchUpload,
    downloadAllBatchArtifacts,
    resetBatch,
  };
}
