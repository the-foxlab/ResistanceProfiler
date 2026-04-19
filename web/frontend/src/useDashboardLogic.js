import { useEffect, useState } from 'react';
import faviconSrc from './assets/favicon.svg';

const DEFAULT_API_BASE =
  typeof window !== 'undefined' && window.location.port === '5173' ? 'http://127.0.0.1:8000' : '';
const API_BASE = import.meta.env.VITE_RESPRO_API_BASE || DEFAULT_API_BASE;
const API_TOKEN = (import.meta.env.VITE_RESPRO_API_TOKEN || '').trim();

export const PROFILE_MODES = [
  { id: 'vcf', label: 'VCF mode' },
  { id: 'fasta', label: 'FASTA mode' },
];

const MUTATION_COLUMN_LABELS = {
  reference_name: 'Reference',
  gene: 'Gene',
  position: 'Pos',
  reference: 'Reference AA',
  mutation: 'Mutation',
  drug: 'Drug',
  phenotype: 'Phenotype',
  clinical_phenotype: 'Clinical phenotype',
  ic50: 'IC50',
  fold_ic50: 'Fold IC50',
  publication: 'DOI',
  source: 'Source',
  comment: 'Comment',
};

const MUTATION_COLUMN_ORDER = [
  'reference_name',
  'gene',
  'position',
  'reference',
  'mutation',
  'drug',
  'phenotype',
  'clinical_phenotype',
  'ic50',
  'fold_ic50',
  'publication',
  'source',
  'comment',
];

function _mutationColumnSortIndex(columnKey) {
  // Unknown columns are still shown, but pushed behind the known stable order.
  const idx = MUTATION_COLUMN_ORDER.indexOf(columnKey);
  return idx === -1 ? MUTATION_COLUMN_ORDER.length + 100 : idx;
}

function _buildMutationColumns(columnKeys) {
  return [...columnKeys]
    .sort((a, b) => _mutationColumnSortIndex(a) - _mutationColumnSortIndex(b))
    .map((columnKey) => {
      const label = MUTATION_COLUMN_LABELS[columnKey] || columnKey;
      const accessor = (rule) => {
        // Rule positions are stored 0-based in the backend and displayed 1-based in the UI.
        if (columnKey === 'position') {
          const positionValue = Number(rule.position);
          if (Number.isFinite(positionValue)) {
            return String(positionValue + 1);
          }
        }
        const value = rule[columnKey];
        if (value === null || value === undefined) {
          return '';
        }
        return String(value);
      };
      return {
        key: columnKey,
        label,
        accessor,
      };
    });
}

function buildHeaders(baseHeaders = {}) {
  // Attach auth header only when a token exists; local dev can run without auth.
  if (!API_TOKEN) {
    return baseHeaders;
  }
  return {
    ...baseHeaders,
    Authorization: `Bearer ${API_TOKEN}`,
  };
}

function buildApiUrl(path, params = {}) {
  // Drop empty query params so generated URLs stay compact and predictable.
  const filteredParams = Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== '')
  );
  const query = new URLSearchParams(filteredParams);
  const queryString = query.toString();
  return queryString ? `${API_BASE}${path}?${queryString}` : `${API_BASE}${path}`;
}

function formatUserError(message) {
  // Convert backend/internal error wording into actionable UI-friendly messages.
  const lines = String(message || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  let normalized = lines.length > 0 ? lines[lines.length - 1] : 'The operation failed.';
  normalized = normalized.replace(/^(ValueError|RuntimeError|Exception|OSError):\s*/, '');

  const lowered = normalized.toLowerCase();
  if (lowered.includes('unsupported fasta format')) {
    return 'Unsupported FASTA format. Upload a text FASTA file with a header line starting with >.';
  }
  if (lowered.includes('unsupported vcf format')) {
    return 'Unsupported VCF format. Upload a VCF with standard headers such as ##fileformat and #CHROM.';
  }
  if (lowered.includes('unsupported bam format')) {
    return 'Unsupported BAM format. Upload a BGZF-compressed BAM file.';
  }
  if (lowered.includes('vcf and reference fasta do not match') || lowered.includes('vcf contig names do not match')) {
    return 'VCF and reference FASTA do not match. Use files derived from the same reference sequence.';
  }
  if (lowered.includes('coverage annotation needs a coordinate-sorted bam')) {
    return 'Coverage annotation needs a coordinate-sorted BAM. The server could not create an index for this file.';
  }
  if (lowered.includes('bam and reference fasta do not match')) {
    return 'BAM and reference FASTA do not match. Use files derived from the same reference sequence.';
  }
  if (normalized.startsWith('Request failed:')) {
    return 'The request failed.';
  }
  return normalized;
}

function formatResultTimestamp(timestamp) {
  if (!timestamp) {
    return 'n/a';
  }

  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) {
    return timestamp;
  }

  return parsed.toLocaleString();
}

async function apiGet(path, params = {}) {
  // Shared fetch helper keeps all GET error handling consistent.
  const response = await fetch(buildApiUrl(path, params), {
    headers: buildHeaders(),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(formatUserError(payload.detail || `Request failed: ${response.status}`));
  }
  return response.json();
}

async function apiPost(path, body) {
  // Shared POST helper centralizes auth/error handling.
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: buildHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(formatUserError(payload.detail || `Request failed: ${response.status}`));
  }
  return response.json();
}

async function apiUpload(path, file, onProgress = null) {
  // Use XHR so upload progress events can be surfaced in the UI.
  const formData = new FormData();
  formData.append('file', file);

  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', `${API_BASE}${path}`);

    const headers = buildHeaders();
    Object.entries(headers).forEach(([key, value]) => {
      request.setRequestHeader(key, value);
    });

    if (onProgress) {
      request.upload.onprogress = (event) => {
        if (!event.lengthComputable) {
          return;
        }
        const percent = Math.min(100, Math.round((event.loaded / event.total) * 100));
        onProgress(percent);
      };
    }

    request.onload = () => {
      let payload = {};
      if (request.responseText) {
        try {
          payload = JSON.parse(request.responseText);
        } catch {
          payload = {};
        }
      }
      if (request.status >= 200 && request.status < 300) {
        resolve(payload);
        return;
      }
      reject(new Error(formatUserError(payload.detail || `Upload failed: ${request.status}`)));
    };

    request.onerror = () => {
      reject(new Error('Upload failed: network error'));
    };

    request.send(formData);
  });
}

export function useDashboardLogic() {
  // Profile input state for each supported workflow mode.
  const [vcfInput, setVcfInput] = useState({
    vcf_path: '',
    ref_fasta_path: '',
    bam_path: null,
    sample: 'sample',
  });
  const [fastaInput, setFastaInput] = useState({
    fasta_path: '',
    sample: 'sample',
  });
  const [rules, setRules] = useState([]);
  const [databases, setDatabases] = useState([]);
  const [selectedDatabaseId, setSelectedDatabaseId] = useState('');
  const [uploadedPaths, setUploadedPaths] = useState([]);
  const [reportPaths, setReportPaths] = useState([]);
  const [status, setStatus] = useState('Ready (backend startup configuration active)');
  const [sessionResults, setSessionResults] = useState([]);
  const [selectedProfileReportPath, setSelectedProfileReportPath] = useState('');
  const [isProcessingFasta, setIsProcessingFasta] = useState(false);
  const [isProcessingVcf, setIsProcessingVcf] = useState(false);

  const [mutationFilter, setMutationFilter] = useState('');
  const [mutationFilterColumn, setMutationFilterColumn] = useState('-1');
  const [mutationSortColumn, setMutationSortColumn] = useState(null);
  const [mutationSortAsc, setMutationSortAsc] = useState(true);
  const [mutationColumnKeys, setMutationColumnKeys] = useState([]);
  const [mutationPlotMeta, setMutationPlotMeta] = useState({ references: [], genes: [] });
  const [mutationsLoaded, setMutationsLoaded] = useState(false);
  const [activeMode, setActiveMode] = useState('profile');
  const [activeProfileMode, setActiveProfileMode] = useState('vcf');
  const [inlineReportPath, setInlineReportPath] = useState('');
  const [inlineReportLabel, setInlineReportLabel] = useState('');
  const [uploadProgress, setUploadProgress] = useState({
    percent: 0,
    fileName: '',
  });

  // Resolve currently selected database once for all consumers.
  const selectedDatabase = databases.find((item) => item.id === selectedDatabaseId) || null;

  useEffect(() => {
    const initData = async () => {
      try {
        setStatus('Loading available databases...');
        const payload = await apiGet('/api/databases');
        const items = payload.data.items || [];
        setDatabases(items);
        if (items.length > 0) {
          setSelectedDatabaseId(items[0].id);
          return;
        }
        setStatus('No databases available');
      } catch (error) {
        setStatus(`Error loading data: ${error.message}`);
      }
    };
    initData();
  }, []);

  useEffect(() => {
    if (!selectedDatabaseId) {
      return;
    }
    const loadMutationsForDb = async () => {
      // Mutation browser + database charts both read from this payload.
      setStatus('Loading mutations from database...');
      try {
        const payload = await apiGet('/api/mutations');
        const items = payload.data.items || [];
        const columns = payload.data.columns || (items.length > 0 ? Object.keys(items[0]) : []);
        const plotMeta = payload.data.plot_meta || { references: [], genes: [] };
        setRules(items);
        setMutationColumnKeys(columns);
        setMutationPlotMeta(plotMeta);
        setMutationsLoaded(true);
        const databaseName = selectedDatabase ? selectedDatabase.display_name : selectedDatabaseId;
        setStatus(`Database ${databaseName} with ${payload.data.count} mutations loaded`);
      } catch (error) {
        setStatus(`Error loading mutations: ${error.message}`);
      }
    };
    loadMutationsForDb();
  }, [selectedDatabase, selectedDatabaseId]);

  const parseValue = (text) => {
    // Sorting prefers numeric comparison when possible, otherwise case-insensitive text.
    const raw = (text || '').trim();
    const num = Number(raw);
    if (!Number.isNaN(num) && raw !== '') {
      return { kind: 'num', value: num };
    }
    return { kind: 'txt', value: raw.toLowerCase() };
  };

  const mutationColumns = _buildMutationColumns(
    mutationColumnKeys.length > 0
      ? mutationColumnKeys
      : (rules[0] ? Object.keys(rules[0]) : [])
  );

  const filterMutations = (rulesList) => {
    // Column filter supports either one selected column or "search in all columns".
    if (!mutationFilter) {
      return rulesList;
    }
    const query = mutationFilter.toLowerCase();
    const colIdx = Number(mutationFilterColumn);

    return rulesList.filter((rule) => {
      if (colIdx === -1) {
        const haystack = mutationColumns.map((column) => column.accessor(rule))
          .join(' ')
          .toLowerCase();
        return haystack.includes(query);
      }
      const selectedColumn = mutationColumns[colIdx];
      const cellText = selectedColumn ? selectedColumn.accessor(rule) : '';
      return cellText.toLowerCase().includes(query);
    });
  };

  const sortMutations = (rulesToSort) => {
    if (mutationSortColumn === null) {
      return rulesToSort;
    }

    const selectedColumn = mutationColumns[mutationSortColumn];
    const getColValue = selectedColumn ? selectedColumn.accessor : null;
    if (!getColValue) {
      return rulesToSort;
    }

    return [...rulesToSort].sort((a, b) => {
      const av = parseValue(String(getColValue(a)));
      const bv = parseValue(String(getColValue(b)));

      let cmp = 0;
      if (av.kind === 'num' && bv.kind === 'num') {
        cmp = av.value - bv.value;
      } else if (av.value < bv.value) {
        cmp = -1;
      } else if (av.value > bv.value) {
        cmp = 1;
      }
      return mutationSortAsc ? cmp : -cmp;
    });
  };

  const displayedRules = sortMutations(filterMutations(rules));

  // Reports are shown newest first for quick access after job completion.
  const reportOptions = sessionResults
    .map((result) => ({
      path: result.report_html_path,
      label: `${result.sample_name} (${result.reference_name}) - ${formatResultTimestamp(result.created_at)}`,
      mode: result.mode,
    }))
    .reverse();

  const selectedReportOption = reportOptions.find((option) => option.path === selectedProfileReportPath) || null;

  const buildReportUrl = (reportPath) => {
    return buildApiUrl('/api/report', {
      path: reportPath,
      token: API_TOKEN || undefined,
    });
  };

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

  useEffect(() => {
    const cleanupUrl = (() => {
      const params = new URLSearchParams();
      if (API_TOKEN) {
        params.set('token', API_TOKEN);
      }
      const query = params.toString();
      return query ? `${API_BASE}/api/session/cleanup?${query}` : `${API_BASE}/api/session/cleanup`;
    })();

    const handlePageHide = () => {
      if (uploadedPaths.length === 0 && reportPaths.length === 0) {
        return;
      }
      // Use sendBeacon during unload when available to avoid dropped cleanup requests.
      const payload = JSON.stringify({ upload_paths: uploadedPaths, report_paths: reportPaths });
      if (navigator.sendBeacon) {
        const blob = new Blob([payload], { type: 'application/json' });
        navigator.sendBeacon(cleanupUrl, blob);
        return;
      }
      fetch(cleanupUrl, {
        method: 'POST',
        headers: buildHeaders({ 'Content-Type': 'application/json' }),
        body: payload,
        keepalive: true,
      }).catch(() => {
        // Ignore cleanup errors during page unload.
      });
    };

    window.addEventListener('pagehide', handlePageHide);
    return () => {
      window.removeEventListener('pagehide', handlePageHide);
    };
  }, [uploadedPaths, reportPaths]);

  useEffect(() => {
    // Ensure the app uses the bundled SVG favicon in all entry modes.
    let link = document.querySelector('link[rel="icon"]');
    if (!link) {
      link = document.createElement('link');
      link.setAttribute('rel', 'icon');
      document.head.appendChild(link);
    }
    link.setAttribute('type', 'image/svg+xml');
    link.setAttribute('href', faviconSrc);
  }, []);

  const pollJob = async (jobId) => {
    // Profiling runs asynchronously on the backend, so poll until final state.
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      const payload = await apiGet(`/api/jobs/${jobId}`);
      if (payload.status === 'succeeded') return payload.result;
      if (payload.status === 'failed') throw new Error(formatUserError(payload.error || 'Job failed'));
      setStatus(`Job running (${jobId.slice(0, 8)}...)`);
    }
  };

  const submitFasta = async () => {
    setIsProcessingFasta(true);
    setStatus('Submitting FASTA profiling job...');
    try {
      const submitResponse = await apiPost('/api/profile/fasta', {
        ...fastaInput,
        aligner: 'mappy',
        threads: 1,
      });
      setStatus(`Job queued (${submitResponse.job_id.slice(0, 8)}...)`);
      const result = await pollJob(submitResponse.job_id);
      // Keep a local history of run results for the report selector.
      setSessionResults((prev) => [...prev, result]);
      addReportPath(result.report_html_path);
      setSelectedProfileReportPath(result.report_html_path);
      setInlineReportPath(result.report_html_path);
      setInlineReportLabel(`${result.sample_name} (${result.reference_name}) - ${formatResultTimestamp(result.created_at)}`);
      if ((result.resistance_hits || 0) === 0) {
        setStatus('FASTA profiling finished. No database matches were found for this sample.');
      } else {
        setStatus('FASTA profiling finished. Database matches were found.');
      }
    } catch (error) {
      setStatus(formatUserError(error.message));
    } finally {
      setIsProcessingFasta(false);
    }
  };

  const submitVcf = async () => {
    setIsProcessingVcf(true);
    setStatus('Submitting VCF profiling job...');
    try {
      const submitResponse = await apiPost('/api/profile/vcf', {
        ...vcfInput,
        aligner: 'mappy',
        threads: 1,
        min_af: 0.01,
        min_depth: 10,
      });
      setStatus(`Job queued (${submitResponse.job_id.slice(0, 8)}...)`);
      const result = await pollJob(submitResponse.job_id);
      setSessionResults((prev) => [...prev, result]);
      addReportPath(result.report_html_path);
      setSelectedProfileReportPath(result.report_html_path);
      setInlineReportPath(result.report_html_path);
      setInlineReportLabel(`${result.sample_name} (${result.reference_name}) - ${formatResultTimestamp(result.created_at)}`);
      if ((result.resistance_hits || 0) === 0) {
        setStatus('VCF profiling finished. No database matches were found for this sample.');
      } else {
        setStatus('VCF profiling finished. Database matches were found.');
      }
    } catch (error) {
      setStatus(formatUserError(error.message));
    } finally {
      setIsProcessingVcf(false);
    }
  };

  const uploadFile = async (file, fileType, onSuccess) => {
    // Shared upload path for FASTA/VCF/reference/BAM inputs.
    setStatus(`Uploading ${fileType.toUpperCase()} file (${file.name})...`);
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
      setStatus(`${fileType.toUpperCase()} file uploaded successfully.`);
    } catch (error) {
      setStatus(formatUserError(error.message));
    }
  };

  const uploadFastaFile = async (file) => {
    await uploadFile(file, 'fasta', (path) => {
      setFastaInput((prev) => ({ ...prev, fasta_path: path }));
    });
  };

  const uploadVcfFile = async (file) => {
    await uploadFile(file, 'vcf', (path) => {
      setVcfInput((prev) => ({ ...prev, vcf_path: path }));
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

  const isProfileBusy = activeProfileMode === 'fasta' ? isProcessingFasta : isProcessingVcf;

  const runSelectedProfile = async () => {
    // Dispatch to the selected profiling workflow from one button handler.
    if (activeProfileMode === 'fasta') {
      await submitFasta();
      return;
    }
    await submitVcf();
  };

  const openSelectedReportInline = () => {
    if (!selectedProfileReportPath) {
      return;
    }
    setInlineReportPath(selectedProfileReportPath);
    setInlineReportLabel(selectedReportOption ? selectedReportOption.label : 'Selected report');
  };

  const downloadMutationsAsTsv = () => {
    // Export exactly what is currently visible (after filter/sort), not raw backend order.
    const headers = mutationColumns.map((column) => column.label);
    const lines = [headers.join('\t')];
    displayedRules.forEach((rule) => {
      const row = mutationColumns.map((column) => {
        const raw = column.accessor(rule);
        return String(raw ?? '').replace(/\t/g, ' ').replace(/\r?\n/g, ' ');
      });
      lines.push(row.join('\t'));
    });

    const blob = new Blob([`${lines.join('\n')}\n`], { type: 'text/tab-separated-values;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = 'mutations.tsv';
    anchor.click();
    URL.revokeObjectURL(href);
  };

  return {
    // Expose one stable object so the view component stays presentation-focused.
    API_BASE,
    PROFILE_MODES,
    vcfInput,
    setVcfInput,
    fastaInput,
    setFastaInput,
    rules,
    databases,
    selectedDatabase,
    selectedDatabaseId,
    setSelectedDatabaseId,
    status,
    selectedProfileReportPath,
    setSelectedProfileReportPath,
    mutationFilter,
    setMutationFilter,
    mutationFilterColumn,
    setMutationFilterColumn,
    mutationSortColumn,
    setMutationSortColumn,
    mutationSortAsc,
    setMutationSortAsc,
    mutationsLoaded,
    activeMode,
    setActiveMode,
    activeProfileMode,
    setActiveProfileMode,
    inlineReportPath,
    inlineReportLabel,
    mutationColumns,
    mutationPlotMeta,
    displayedRules,
    reportOptions,
    isProfileBusy,
    runSelectedProfile,
    openSelectedReportInline,
    buildReportUrl,
    uploadFastaFile,
    uploadVcfFile,
    uploadReferenceFile,
    uploadBamFile,
    downloadMutationsAsTsv,
    uploadProgress,
  };
}
