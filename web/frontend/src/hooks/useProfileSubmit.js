import { useRef, useState } from 'react';
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
} from '../api';
import { validateVcfCutoffs } from '../utils';

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
  const isCancellationRequested = useRef(false);
  const currentUploadRef = useRef(null);
  const isUploadCanceledRef = useRef(false);

  const pollJob = async (jobId) => {
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, FRONTEND_CONFIG.profile.jobPollIntervalMs));
      const payload = await apiGet(`/api/jobs/${jobId}`);
      if (!isCancellationRequested.current) {
        setActiveJobStatus(payload.status);
      }
      if (payload.status === 'succeeded') return payload.result;
      if (payload.status === 'failed') throw new Error(formatUserError(payload.error || 'Job failed'));
    }
  };

  const runProfileJob = async (endpoint, payloadFactory, setBusy) => {
    setBusy(true);
    setStatusError('');
    try {
      const payload = payloadFactory();
      const submitResponse = await apiPost(endpoint, payload);
      isCancellationRequested.current = false;
      setActiveJobId(submitResponse.job_id);
      setActiveJobStatus('queued');
      const result = await pollJob(submitResponse.job_id);
      setSessionResults((prev) => [...prev, result]);
      addResultArtifactPaths(result);
      setSelectedProfileReportPath(result.report_html_path);
      setInlineReportPath(result.report_html_path);
    } catch (error) {
      setStatusError(formatUserError(error.message));
    } finally {
      setBusy(false);
      setIsCancelingJob(false);
      setActiveJobId('');
      setActiveJobStatus('');
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
    if (!activeJobId) return;
    setIsCancelingJob(true);
    isCancellationRequested.current = true;
    try {
      await apiDelete(`/api/jobs/${activeJobId}`);
      setActiveJobStatus('canceling');
    } catch (error) {
      setStatusError(formatUserError(error.message));
    } finally {
      setIsCancelingJob(false);
    }
  };

  const uploadFile = async (file, fileType, onSuccess) => {
    isUploadCanceledRef.current = false;
    beginUpload();
    setUploadProgress({ percent: 0, fileName: `${fileType.toUpperCase()} - ${file.name}` });
    try {
      const response = await apiUpload(
        `/api/upload/${fileType}`,
        file,
        (percent) => setUploadProgress((prev) => ({ ...prev, percent })),
        (request) => {
          currentUploadRef.current = request;
        },
      );
      onSuccess(response.upload_id);
      addUploadedPath(response.upload_id);
      setUploadProgress((prev) => ({ ...prev, percent: 100 }));
    } catch (error) {
      if (!isUploadCanceledRef.current) {
        setStatusError(formatUserError(error.message));
      }
    } finally {
      currentUploadRef.current = null;
      endUpload();
    }
  };

  const cancelUpload = () => {
    if (currentUploadRef.current) {
      isUploadCanceledRef.current = true;
      currentUploadRef.current.abort();
    }
  };

  const uploadFastaFile = (file) =>
    uploadFile(file, 'fasta', (uploadId) =>
      setFastaInput((prev) => ({ ...prev, fasta_id: uploadId, input_display_name: file.name })),
    );

  const uploadVcfFile = (file) =>
    uploadFile(file, 'vcf', (uploadId) =>
      setVcfInput((prev) => ({ ...prev, vcf_id: uploadId, input_display_name: file.name })),
    );

  const uploadReferenceFile = (file) =>
    uploadFile(file, 'fasta', (uploadId) => setVcfInput((prev) => ({ ...prev, reference_id: uploadId })));

  const uploadBamFile = (file) =>
    uploadFile(file, 'bam', (uploadId) => setVcfInput((prev) => ({ ...prev, bam_id: uploadId })));

  const uploadJsonFile = (file) =>
    uploadFile(file, 'json', (uploadId) => setJsonInputId(uploadId));

  const isProfileBusy =
    activeProfileMode === 'fasta' ? isProcessingFasta : activeProfileMode === 'vcf' ? isProcessingVcf : false;
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
