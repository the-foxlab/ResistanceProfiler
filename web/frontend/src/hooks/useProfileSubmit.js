import { useCallback, useEffect, useRef, useState } from 'react';
import { FRONTEND_CONFIG } from '../config';
import {
  apiDelete,
  apiGet,
  apiPost,
  apiUpload,
  buildArtifactUrl,
  buildReportUrl,
  formatPathStem,
  formatUserError,
  isAbortError,
} from '../api';
import { validateVcfCutoffs } from '../utils';

function sleep(ms, signal, wakeRef = null) {
  if (signal?.aborted) {
    return Promise.resolve(false);
  }
  if (ms <= 0) {
    return Promise.resolve(true);
  }
  return new Promise((resolve) => {
    let settled = false;
    const cleanup = () => {
      if (wakeRef && wakeRef.current === wake) {
        wakeRef.current = null;
      }
      if (signal && typeof signal.removeEventListener === 'function') {
        signal.removeEventListener('abort', onAbort);
      }
    };
    const wake = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      cleanup();
      resolve(!signal?.aborted);
    };
    const onAbort = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      cleanup();
      resolve(false);
    };
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(!signal?.aborted);
    }, ms);
    if (wakeRef) {
      wakeRef.current = wake;
    }
    if (signal && typeof signal.addEventListener === 'function') {
      signal.addEventListener('abort', onAbort, { once: true });
    }
  });
}

export function useProfileSubmit({
  setStatusError,
  addUploadedPath,
  addResultArtifactPaths,
  setSessionResults,
  setSelectedProfileReportPath,
  setInlineReportPath,
  databases,
  selectedDatabaseId,
  activeProfileMode,
  setUploadProgress,
  beginUpload,
  endUpload,
}) {
  const [vcfInput, setVcfInput] = useState({
    vcf_id: '',
    input_display_name: '',
    reference_id: '',
    bam_id: null,
    sample: '',
    min_af: FRONTEND_CONFIG.profile.vcf.minAf,
    min_depth: FRONTEND_CONFIG.profile.vcf.minDepth,
  });
  const [fastaInput, setFastaInput] = useState({
    fasta_id: '',
    input_display_name: '',
    sample: '',
  });
  const [jsonInputId, setJsonInputId] = useState('');
  const [isProcessingFasta, setIsProcessingFasta] = useState(false);
  const [isProcessingVcf, setIsProcessingVcf] = useState(false);
  const [isProcessingRegenerate, setIsProcessingRegenerate] = useState(false);
  const [activeJobId, setActiveJobId] = useState('');
  const [activeJobStatus, setActiveJobStatus] = useState('');
  const [isCancelingJob, setIsCancelingJob] = useState(false);
  const isMountedRef = useRef(true);
  const isCancellationRequested = useRef(false);
  const isCancellationConfirmedRef = useRef(false);
  const cancelDeletePromiseRef = useRef(null);
  const wakePollRef = useRef(null);
  const jobTokenRef = useRef(0);
  const jobAbortControllerRef = useRef(null);
  const activeJobIdRef = useRef('');
  const activeBusySetterRef = useRef(null);
  const uploadHandlesRef = useRef(new Map());
  const uploadTokensRef = useRef(new Map());

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      jobTokenRef.current += 1;
      activeJobIdRef.current = '';
      if (jobAbortControllerRef.current) {
        jobAbortControllerRef.current.abort();
        jobAbortControllerRef.current = null;
      }
      for (const handle of uploadHandlesRef.current.values()) {
        handle.canceled = true;
        handle.controller.abort();
        handle.request?.abort?.();
      }
      uploadHandlesRef.current.clear();
    };
  }, []);

  const clearInputUploadState = useCallback((inputKey) => {
    switch (inputKey) {
      case 'fasta':
        setFastaInput((prev) => ({ ...prev, fasta_id: '', input_display_name: '' }));
        break;
      case 'vcf':
        setVcfInput((prev) => ({ ...prev, vcf_id: '', input_display_name: '' }));
        break;
      case 'reference':
        setVcfInput((prev) => ({ ...prev, reference_id: '' }));
        break;
      case 'bam':
        setVcfInput((prev) => ({ ...prev, bam_id: null }));
        break;
      case 'json':
        setJsonInputId('');
        break;
      default:
        break;
    }
  }, []);

  const pollJob = async (jobId, { signal, opToken }) => {
    const basePollIntervalMs = FRONTEND_CONFIG.profile?.jobPollIntervalMs ?? 2000;
    const maxTransientBackoffMs = FRONTEND_CONFIG.batch?.maxPollIntervalMs ?? 30_000;
    const defaultRetryAfterSeconds = FRONTEND_CONFIG.batch?.defaultRetryAfterSeconds ?? 5;
    const maxRetryBackoffSeconds = Math.max(
      defaultRetryAfterSeconds,
      Math.ceil((FRONTEND_CONFIG.batch?.maxPollBackoffMs ?? 60_000) / 1000),
    );
    const maxConsecutiveErrors = FRONTEND_CONFIG.batch?.maxConsecutiveErrors ?? 10;

    let consecutive429s = 0;
    let consecutiveErrors = 0;
    let nextDelayMs = basePollIntervalMs;

    for (;;) {
      if (signal?.aborted || !isMountedRef.current || opToken !== jobTokenRef.current) {
        return null;
      }
      const ok = await sleep(nextDelayMs, signal, wakePollRef);
      if (!ok || signal?.aborted || !isMountedRef.current || opToken !== jobTokenRef.current) {
        return null;
      }
      nextDelayMs = basePollIntervalMs;

      let payload;
      try {
        payload = await apiGet(`/api/jobs/${jobId}`, { signal });
        consecutive429s = 0;
        consecutiveErrors = 0;
      } catch (error) {
        if (
          isAbortError(error)
          || signal?.aborted
          || !isMountedRef.current
          || opToken !== jobTokenRef.current
        ) {
          return null;
        }
        const status = error?.status;
        if (status === 429) {
          const waitSeconds = Number.isFinite(error?.retryAfterSeconds) && error.retryAfterSeconds > 0
            ? Math.ceil(error.retryAfterSeconds)
            : Math.min(maxRetryBackoffSeconds, defaultRetryAfterSeconds * (2 ** Math.min(consecutive429s, 4)));
          consecutive429s += 1;
          nextDelayMs = waitSeconds * 1000;
          continue;
        }
        if (Number.isInteger(status) && status >= 400 && status < 500 && status !== 408) {
          throw error;
        }
        consecutiveErrors += 1;
        if (consecutiveErrors >= maxConsecutiveErrors) {
          throw error;
        }
        nextDelayMs = Math.min(
          maxTransientBackoffMs,
          Math.max(10, basePollIntervalMs) * (2 ** consecutiveErrors),
        );
        continue;
      }

      if (signal?.aborted || !isMountedRef.current || opToken !== jobTokenRef.current) {
        return null;
      }
      if (!isCancellationRequested.current) {
        setActiveJobStatus(payload.status);
      }
      if (payload.status === 'succeeded') {
        if (cancelDeletePromiseRef.current) {
          await cancelDeletePromiseRef.current.catch(() => {});
        }
        if (signal?.aborted || !isMountedRef.current || opToken !== jobTokenRef.current) {
          return null;
        }
        if (isCancellationConfirmedRef.current) {
          throw new Error('Job canceled by user.');
        }
        return payload.result;
      }
      if (payload.status === 'failed') {
        throw new Error(formatUserError(payload.error || 'Job failed'));
      }
      if (payload.status === 'canceled') {
        throw new Error(formatUserError(payload.error || 'Job canceled by user.'));
      }
    }
  };

  const runProfileJob = async (endpoint, payloadFactory, setBusy) => {
    const opToken = ++jobTokenRef.current;
    if (jobAbortControllerRef.current) {
      jobAbortControllerRef.current.abort();
    }
    if (activeBusySetterRef.current && activeBusySetterRef.current !== setBusy) {
      activeBusySetterRef.current(false);
    }
    activeBusySetterRef.current = setBusy;

    const controller = new AbortController();
    jobAbortControllerRef.current = controller;

    isCancellationRequested.current = false;
    isCancellationConfirmedRef.current = false;
    cancelDeletePromiseRef.current = null;
    activeJobIdRef.current = '';
    setActiveJobId('');
    setActiveJobStatus('');
    setIsCancelingJob(false);

    setBusy(true);
    setStatusError('');
    try {
      const payload = payloadFactory();
      const submitResponse = await apiPost(endpoint, payload, { signal: controller.signal });
      if (
        controller.signal.aborted
        || !isMountedRef.current
        || opToken !== jobTokenRef.current
      ) {
        return;
      }
      activeJobIdRef.current = submitResponse.job_id;
      setActiveJobId(submitResponse.job_id);
      setActiveJobStatus('queued');
      const result = await pollJob(submitResponse.job_id, {
        signal: controller.signal,
        opToken,
      });
      if (
        !result
        || controller.signal.aborted
        || !isMountedRef.current
        || opToken !== jobTokenRef.current
        || isCancellationConfirmedRef.current
      ) {
        return;
      }
      setSessionResults((prev) => [...prev, result]);
      addResultArtifactPaths(result);
      setSelectedProfileReportPath(result.report_html_path);
      setInlineReportPath(result.report_html_path);
    } catch (error) {
      if (
        !isAbortError(error)
        && !controller.signal.aborted
        && isMountedRef.current
        && opToken === jobTokenRef.current
      ) {
        setStatusError(formatUserError(error.message));
      }
    } finally {
      if (jobAbortControllerRef.current === controller) {
        jobAbortControllerRef.current = null;
      }
      if (isMountedRef.current) {
        if (opToken === jobTokenRef.current || activeBusySetterRef.current !== setBusy) {
          setBusy(false);
        }
        if (opToken === jobTokenRef.current) {
          activeBusySetterRef.current = null;
          activeJobIdRef.current = '';
          setIsCancelingJob(false);
          setActiveJobId('');
          setActiveJobStatus('');
        }
      }
    }
  };

  const submitFasta = () =>
    runProfileJob(
      '/api/profile/fasta',
      () => ({
        fasta_id: fastaInput.fasta_id,
        input_display_name: fastaInput.input_display_name,
        sample: fastaInput.sample || formatPathStem(fastaInput.input_display_name),
        database_id: selectedDatabaseId,
        threads: FRONTEND_CONFIG.profile.threads,
      }),
      setIsProcessingFasta,
    );

  const runExampleProfile = () => {
    const database = databases.find((item) => item.id === selectedDatabaseId) || null;
    const sample = database ? `${database.display_name || database.id} example` : 'example';
    return runProfileJob(
      '/api/profile/fasta',
      () => ({
        use_example: true,
        sample,
        database_id: selectedDatabaseId,
        threads: FRONTEND_CONFIG.profile.threads,
      }),
      setIsProcessingFasta,
    );
  };

  const submitVcf = () =>
    runProfileJob(
      '/api/profile/vcf',
      () => {
        validateVcfCutoffs(vcfInput.min_af, vcfInput.min_depth);
        return {
          vcf_id: vcfInput.vcf_id,
          reference_id: vcfInput.reference_id,
          bam_id: vcfInput.bam_id,
          input_display_name: vcfInput.input_display_name,
          sample: vcfInput.sample || formatPathStem(vcfInput.input_display_name),
          min_af: vcfInput.min_af,
          min_depth: vcfInput.min_depth,
          database_id: selectedDatabaseId,
          threads: FRONTEND_CONFIG.profile.threads,
        };
      },
      setIsProcessingVcf,
    );

  const runRegenerateFromJson = async () => {
    if (!jsonInputId) {
      setStatusError('Upload a valid results JSON file first.');
      return;
    }
    await runProfileJob('/api/regenerate/json', () => ({ json_id: jsonInputId }), setIsProcessingRegenerate);
  };

  const cancelActiveJob = async () => {
    const targetJobId = activeJobIdRef.current || activeJobId;
    if (!targetJobId || cancelDeletePromiseRef.current) return;
    const opToken = jobTokenRef.current;
    setIsCancelingJob(true);
    isCancellationRequested.current = true;
    const deletePromise = (async () => {
      try {
        await apiDelete(`/api/jobs/${targetJobId}`, {
          signal: jobAbortControllerRef.current?.signal,
        });
        if (
          isMountedRef.current
          && opToken === jobTokenRef.current
          && activeJobIdRef.current === targetJobId
        ) {
          isCancellationConfirmedRef.current = true;
          setActiveJobStatus('canceling');
          wakePollRef.current?.();
        }
      } catch (error) {
        if (isAbortError(error)) return;
        if (
          isMountedRef.current
          && opToken === jobTokenRef.current
          && activeJobIdRef.current === targetJobId
        ) {
          isCancellationRequested.current = false;
          setStatusError(formatUserError(error.message));
        }
      } finally {
        if (cancelDeletePromiseRef.current === deletePromise) {
          cancelDeletePromiseRef.current = null;
        }
        if (isMountedRef.current && opToken === jobTokenRef.current) {
          setIsCancelingJob(false);
        }
      }
    })();
    cancelDeletePromiseRef.current = deletePromise;
    await deletePromise;
  };

  const uploadFile = async (inputKey, file, fileType, onSuccess) => {
    if (!file) return false;
    const existingHandle = uploadHandlesRef.current.get(inputKey);
    if (existingHandle) {
      existingHandle.canceled = true;
      existingHandle.controller.abort();
      existingHandle.request?.abort?.();
      uploadHandlesRef.current.delete(inputKey);
    }

    const nextToken = (uploadTokensRef.current.get(inputKey) || 0) + 1;
    uploadTokensRef.current.set(inputKey, nextToken);
    clearInputUploadState(inputKey);

    const controller = new AbortController();
    const handle = {
      controller,
      request: null,
      canceled: false,
      token: nextToken,
      inputKey,
    };
    uploadHandlesRef.current.set(inputKey, handle);

    beginUpload();
    setUploadProgress({ percent: 0, fileName: `${fileType.toUpperCase()} - ${file.name}` });
    try {
      const response = await apiUpload(
        `/api/upload/${fileType}`,
        file,
        (percent) => {
          if (
            isMountedRef.current
            && !handle.canceled
            && uploadTokensRef.current.get(inputKey) === nextToken
          ) {
            setUploadProgress((prev) => ({ ...prev, percent }));
          }
        },
        (request) => {
          handle.request = request;
          if (handle.canceled || controller.signal.aborted) {
            request.abort?.();
          }
        },
        { signal: controller.signal },
      );
      if (response?.upload_id) {
        addUploadedPath(response.upload_id);
      }
      if (
        !isMountedRef.current
        || handle.canceled
        || controller.signal.aborted
        || uploadTokensRef.current.get(inputKey) !== nextToken
      ) {
        return false;
      }
      onSuccess(response.upload_id);
      setUploadProgress((prev) => ({ ...prev, percent: 100 }));
      return true;
    } catch (error) {
      if (
        isMountedRef.current
        && !handle.canceled
        && !controller.signal.aborted
        && !isAbortError(error)
        && uploadTokensRef.current.get(inputKey) === nextToken
      ) {
        clearInputUploadState(inputKey);
        setStatusError(formatUserError(error.message));
      }
      return false;
    } finally {
      if (uploadHandlesRef.current.get(inputKey) === handle) {
        uploadHandlesRef.current.delete(inputKey);
      }
      endUpload();
    }
  };

  const cancelUpload = useCallback((inputKey) => {
    if (typeof inputKey === 'string' && inputKey.length > 0) {
      const handle = uploadHandlesRef.current.get(inputKey);
      if (!handle) return;
      handle.canceled = true;
      uploadTokensRef.current.set(inputKey, (uploadTokensRef.current.get(inputKey) || 0) + 1);
      uploadHandlesRef.current.delete(inputKey);
      clearInputUploadState(inputKey);
      handle.controller.abort();
      handle.request?.abort?.();
      return;
    }
    if (uploadHandlesRef.current.size === 0) return;
    const handles = Array.from(uploadHandlesRef.current.entries());
    uploadHandlesRef.current.clear();
    for (const [key, handle] of handles) {
      handle.canceled = true;
      uploadTokensRef.current.set(key, (uploadTokensRef.current.get(key) || 0) + 1);
      clearInputUploadState(key);
      handle.controller.abort();
      handle.request?.abort?.();
    }
  }, [clearInputUploadState]);

  const uploadFastaFile = (file) =>
    uploadFile('fasta', file, 'fasta', (uploadId) =>
      setFastaInput((prev) => ({ ...prev, fasta_id: uploadId, input_display_name: file.name })),
    );

  const uploadVcfFile = (file) =>
    uploadFile('vcf', file, 'vcf', (uploadId) =>
      setVcfInput((prev) => ({ ...prev, vcf_id: uploadId, input_display_name: file.name })),
    );

  const uploadReferenceFile = (file) =>
    uploadFile('reference', file, 'fasta', (uploadId) =>
      setVcfInput((prev) => ({ ...prev, reference_id: uploadId })),
    );

  const uploadBamFile = (file) =>
    uploadFile('bam', file, 'bam', (uploadId) =>
      setVcfInput((prev) => ({ ...prev, bam_id: uploadId })),
    );

  const uploadJsonFile = (file) =>
    uploadFile('json', file, 'json', (uploadId) => setJsonInputId(uploadId));

  const isProfileBusy = isProcessingFasta || isProcessingVcf;
  const canCancelJob = Boolean(activeJobId) && ['queued', 'running'].includes(activeJobStatus);
  const runSelectedProfile = () => (activeProfileMode === 'fasta' ? submitFasta() : submitVcf());

  return {
    vcfInput,
    setVcfInput,
    fastaInput,
    setFastaInput,
    jsonInputId,
    isRegenerateBusy: isProcessingRegenerate,
    isCancelingJob,
    buildReportUrl,
    buildArtifactUrl,
    cancelActiveJob,
    runSelectedProfile,
    runExampleProfile,
    uploadFastaFile,
    uploadVcfFile,
    uploadReferenceFile,
    uploadBamFile,
    uploadJsonFile,
    runRegenerateFromJson,
    cancelUpload,
    isProfileBusy,
    canCancelJob,
  };
}
