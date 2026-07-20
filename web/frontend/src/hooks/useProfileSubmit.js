import { useRef, useState } from 'react';
import { FRONTEND_CONFIG } from '../config';
import { apiGet, apiDelete, apiPost, apiUpload, formatUserError, formatResultTimestamp, buildApiUrl, formatPathStem } from '../api';

export function useProfileSubmit({
  setStatusError,
  addReportPath,
  addUploadedPath,
  addResultArtifactPaths,
  setSessionResults,
  setSelectedProfileReportPath,
  setInlineReportPath,
  setInlineReportLabel,
  databases,
  selectedDatabaseId,
  activeProfileMode,
  analyzeSubMode,
  setUploadProgress,
}) {
  const [vcfInput, setVcfInput] = useState({
    vcf_path: '',
    input_display_name: '',
    ref_fasta_path: '',
    bam_path: null,
    sample: '',
    min_af: FRONTEND_CONFIG.profile.vcf.minAf,
    min_depth: FRONTEND_CONFIG.profile.vcf.minDepth,
  });
  const [fastaInput, setFastaInput] = useState({
    fasta_path: '',
    input_display_name: '',
    sample: '',
  });
  const [jsonInputPath, setJsonInputPath] = useState('');
  const [isProcessingFasta, setIsProcessingFasta] = useState(false);
  const [isProcessingVcf, setIsProcessingVcf] = useState(false);
  const [isProcessingRegenerate, setIsProcessingRegenerate] = useState(false);
  const [activeJobId, setActiveJobId] = useState('');
  const [activeJobStatus, setActiveJobStatus] = useState('');
  const [isCancelingJob, setIsCancelingJob] = useState(false);
  const isCancellationRequested = useRef(false);

  const selectedDatabase = databases.find((item) => item.id === selectedDatabaseId) || null;

  const buildReportUrl = (reportPath) => {
    return buildApiUrl('/api/report', { path: reportPath });
  };

  const buildArtifactUrl = (artifactPath) => {
    return buildApiUrl('/api/artifact', { path: artifactPath });
  };

  const pollJob = async (jobId) => {
    // Profiling runs asynchronously on the backend, so poll until final state.
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

  const submitFasta = async () => {
    const databaseId = selectedDatabaseId;
    setIsProcessingFasta(true);
    setStatusError('');
    try {
      const fastaPayload = {
        ...fastaInput,
        sample: fastaInput.sample || formatPathStem(fastaInput.input_display_name),
        database_id: databaseId,
        threads: FRONTEND_CONFIG.profile.threads,
      };
      const submitResponse = await apiPost('/api/profile/fasta', fastaPayload);
      isCancellationRequested.current = false;
      setActiveJobId(submitResponse.job_id);
      setActiveJobStatus('queued');
      const result = await pollJob(submitResponse.job_id);
      // Keep a local history of run results for the report selector.
      setSessionResults((prev) => [...prev, result]);
      addResultArtifactPaths(result);
      setSelectedProfileReportPath(result.report_html_path);
      setInlineReportPath(result.report_html_path);
      setInlineReportLabel(`${result.sample_name} (${result.reference_name}) - ${formatResultTimestamp(result.created_at)}`);
    } catch (error) {
      setStatusError(formatUserError(error.message));
    } finally {
      setIsProcessingFasta(false);
      setIsCancelingJob(false);
      setActiveJobId('');
      setActiveJobStatus('');
    }
  };

  const submitVcf = async () => {
    const databaseId = selectedDatabaseId;
    setIsProcessingVcf(true);
    setStatusError('');
    try {
      if (!Number.isFinite(vcfInput.min_af) || vcfInput.min_af < 0 || vcfInput.min_af > 1) {
        throw new Error('Frequency cutoff (min AF) must be a number between 0 and 1.');
      }
      if (!Number.isInteger(vcfInput.min_depth) || vcfInput.min_depth < 0) {
        throw new Error('Coverage cutoff (min depth) must be an integer greater than or equal to 0.');
      }

      const vcfPayload = {
        ...vcfInput,
        sample: vcfInput.sample || formatPathStem(vcfInput.input_display_name),
        database_id: databaseId,
        threads: FRONTEND_CONFIG.profile.threads,
      };
      const submitResponse = await apiPost('/api/profile/vcf', vcfPayload);
      isCancellationRequested.current = false;
      setActiveJobId(submitResponse.job_id);
      setActiveJobStatus('queued');
      const result = await pollJob(submitResponse.job_id);
      setSessionResults((prev) => [...prev, result]);
      addResultArtifactPaths(result);
      setSelectedProfileReportPath(result.report_html_path);
      setInlineReportPath(result.report_html_path);
      setInlineReportLabel(`${result.sample_name} (${result.reference_name}) - ${formatResultTimestamp(result.created_at)}`);
    } catch (error) {
      setStatusError(formatUserError(error.message));
    } finally {
      setIsProcessingVcf(false);
      setIsCancelingJob(false);
      setActiveJobId('');
      setActiveJobStatus('');
    }
  };

  const cancelActiveJob = async () => {
    if (!activeJobId) {
      return;
    }

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
    // Shared upload path for FASTA/VCF/reference/BAM inputs.
    setUploadProgress({
      percent: 0,
      fileName: `${fileType.toUpperCase()} - ${file.name}`,
    });
    try {
      const response = await apiUpload(`/api/upload/${fileType}`, file, (percent) => {
        setUploadProgress((prev) => ({
          ...prev,
          percent,
        }));
      });
      onSuccess(response.file_path);
      addUploadedPath(response.file_path);
      setUploadProgress((prev) => ({
        ...prev,
        percent: 100,
      }));
    } catch (error) {
      setStatusError(formatUserError(error.message));
    }
  };

  const uploadFastaFile = async (file) => {
    await uploadFile(file, 'fasta', (path) => {
      setFastaInput((prev) => ({ ...prev, fasta_path: path, input_display_name: file.name }));
    });
  };

  const uploadVcfFile = async (file) => {
    await uploadFile(file, 'vcf', (path) => {
      setVcfInput((prev) => ({ ...prev, vcf_path: path, input_display_name: file.name }));
    });
  };

  const uploadReferenceFile = async (file) => {
    await uploadFile(file, 'fasta', (path) => {
      setVcfInput((prev) => ({ ...prev, ref_fasta_path: path }));
    });
  };

  const uploadBamFile = async (file) => {
    await uploadFile(file, 'bam', (path) => {
      setVcfInput((prev) => ({ ...prev, bam_path: path }));
    });
  };

  const uploadJsonFile = async (file) => {
    await uploadFile(file, 'json', (path) => {
      setJsonInputPath(path);
    });
  };

  const runRegenerateFromJson = async () => {
    if (!jsonInputPath) {
      setStatusError('Upload a valid results JSON file first.');
      return;
    }

    setIsProcessingRegenerate(true);
    setStatusError('');
    try {
      const submitResponse = await apiPost('/api/regenerate/json', {
        json_path: jsonInputPath,
      });
      isCancellationRequested.current = false;
      setActiveJobId(submitResponse.job_id);
      setActiveJobStatus('queued');
      const result = await pollJob(submitResponse.job_id);
      setSessionResults((prev) => [...prev, result]);
      addResultArtifactPaths(result);
      setSelectedProfileReportPath(result.report_html_path);
      setInlineReportPath(result.report_html_path);
      setInlineReportLabel(`${result.sample_name} (${result.reference_name}) - ${formatResultTimestamp(result.created_at)}`);
    } catch (error) {
      setStatusError(formatUserError(error.message));
    } finally {
      setIsProcessingRegenerate(false);
      setIsCancelingJob(false);
      setActiveJobId('');
      setActiveJobStatus('');
    }
  };

  const isProfileBusy = activeProfileMode === 'fasta'
    ? isProcessingFasta
    : activeProfileMode === 'vcf'
      ? isProcessingVcf
      : false;

  const canCancelJob = Boolean(activeJobId) && ['queued', 'running'].includes(activeJobStatus);

  const runSelectedProfile = async () => {
    // Dispatch to the selected profiling workflow from one button handler.
    if (activeProfileMode === 'fasta') {
      await submitFasta();
      return;
    }
    await submitVcf();
  };

  return {
    vcfInput,
    setVcfInput,
    fastaInput,
    setFastaInput,
    jsonInputPath,
    setJsonInputPath,
    isProcessingFasta,
    isProcessingVcf,
    isProcessingRegenerate,
    activeJobId,
    activeJobStatus,
    isCancelingJob,
    buildReportUrl,
    buildArtifactUrl,
    cancelActiveJob,
    runSelectedProfile,
    uploadFastaFile,
    uploadVcfFile,
    uploadReferenceFile,
    uploadBamFile,
    uploadJsonFile,
    runRegenerateFromJson,
    isProfileBusy,
    canCancelJob,
  };
}
