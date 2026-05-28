import { useEffect, useState } from 'react';
import faviconSrc from './assets/favicon.svg';
import { FRONTEND_CONFIG } from './config';

const API_BASE = FRONTEND_CONFIG.apiBase;
const API_TOKEN = FRONTEND_CONFIG.apiToken;

export const PROFILE_MODES = [
  { id: 'vcf', label: 'VCF mode' },
  { id: 'fasta', label: 'FASTA mode' },
];

const MUTATION_COLUMN_LABELS = {
  reference_name: 'Reference',
  feature: 'Sequence Feature',
  position: 'Pos',
  reference: 'Reference AA',
  mutation: 'Mutation',
  drug: 'Drug',
  drug_group: 'Drug Group',
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
  'feature',
  'position',
  'reference',
  'mutation',
  'drug',
  'drug_group',
  'phenotype',
  'clinical_phenotype',
  'ic50',
  'fold_ic50',
  'publication',
  'source',
  'comment',
];

const FORMULA_COLUMN_LABELS = {
  reference_name: 'Reference',
  drug: 'Drug',
  drug_group: 'Drug Group',
  formula_id: 'Formula ID',
  label: 'Label',
  normalized_expression: 'Expression',
  member_count: 'Members',
  phenotype: 'Phenotype',
  clinical_phenotype: 'Clinical phenotype',
  ic50: 'IC50',
  fold_ic50: 'Fold IC50',
  publication: 'DOI',
  source: 'Source',
  comment: 'Comment',
};

const FORMULA_COLUMN_ORDER = [
  'reference_name',
  'drug',
  'drug_group',
  'formula_id',
  'label',
  'normalized_expression',
  'member_count',
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

function _buildMutationColumns(columnKeys, plotMeta = {}) {
  const drugAliasLookup = _buildDrugAliasLookup(plotMeta);
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
        if (columnKey === 'drug') {
          const drugName = String(rule.drug || '').trim();
          if (!drugName) {
            return '';
          }
          const alias = _resolveDrugAlias(drugName, drugAliasLookup);
          return alias ? `${drugName} (${alias})` : drugName;
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
        sortAccessor: columnKey === 'drug' ? (rule) => String(rule.drug || '') : accessor,
      };
    });
}

function _formulaColumnSortIndex(columnKey) {
  const idx = FORMULA_COLUMN_ORDER.indexOf(columnKey);
  return idx === -1 ? FORMULA_COLUMN_ORDER.length + 100 : idx;
}

function _buildFormulaColumns(columnKeys, plotMeta = {}) {
  const drugAliasLookup = _buildDrugAliasLookup(plotMeta);
  return [...columnKeys]
    .sort((a, b) => _formulaColumnSortIndex(a) - _formulaColumnSortIndex(b))
    .map((columnKey) => {
      const label = FORMULA_COLUMN_LABELS[columnKey] || columnKey;
      const accessor = (formulaRule) => {
        if (columnKey === 'drug') {
          const drugName = String(formulaRule.drug || '').trim();
          if (!drugName) {
            return '';
          }
          const alias = _resolveDrugAlias(drugName, drugAliasLookup);
          return alias ? `${drugName} (${alias})` : drugName;
        }
        const value = formulaRule[columnKey];
        if (value === null || value === undefined) {
          return '';
        }
        return String(value);
      };
      return {
        key: columnKey,
        label,
        accessor,
        sortAccessor: columnKey === 'drug' ? (formulaRule) => String(formulaRule.drug || '') : accessor,
      };
    });
}

function _buildDrugAliasLookup(plotMeta) {
  const aliases = plotMeta?.drug_aliases || {};
  const lookup = new Map();
  Object.entries(aliases).forEach(([drugName, alias]) => {
    const canonical = String(drugName || '').trim().toLowerCase();
    const displayAlias = String(alias || '').trim();
    if (canonical && displayAlias) {
      lookup.set(canonical, displayAlias);
    }
  });
  return lookup;
}

function _resolveDrugAlias(drugName, aliasLookup) {
  return aliasLookup.get(String(drugName || '').trim().toLowerCase()) || '';
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

export function buildApiUrl(path, params = {}) {
  // Drop empty query params so generated URLs stay compact and predictable.
  const filteredParams = Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== '')
  );
  const query = new URLSearchParams(filteredParams);
  const queryString = query.toString();
  return queryString ? `${API_BASE}${path}?${queryString}` : `${API_BASE}${path}`;
}

export function formatUserError(message) {
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
  if (lowered.includes('unsupported json format')) {
    return 'Unsupported JSON format. Upload a valid ResistanceProfiler results JSON file.';
  }
  if (lowered.includes('project database uuid mismatch')) {
    return (
      'Project database UUID mismatch. Database updates currently do not allow regeneration '
      + 'of reports from older database versions.'
    );
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

function formatPathBasename(path) {
  if (!path) {
    return 'n/a';
  }
  const normalized = String(path).replace(/\\/g, '/');
  const parts = normalized.split('/').filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : normalized;
}

function formatPathStem(path) {
  const basename = formatPathBasename(path);
  const dotIndex = basename.lastIndexOf('.');
  if (dotIndex <= 0) {
    return basename;
  }
  return basename.slice(0, dotIndex);
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

async function apiDelete(path) {
  // Shared DELETE helper centralizes auth/error handling.
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'DELETE',
    headers: buildHeaders(),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(formatUserError(payload.detail || `Request failed: ${response.status}`));
  }
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
    input_display_name: '',
    ref_fasta_path: '',
    bam_path: null,
    sample: FRONTEND_CONFIG.defaults.sampleName,
    min_af: FRONTEND_CONFIG.profile.vcf.minAf,
    min_depth: FRONTEND_CONFIG.profile.vcf.minDepth,
  });
  const [fastaInput, setFastaInput] = useState({
    fasta_path: '',
    input_display_name: '',
    sample: FRONTEND_CONFIG.defaults.sampleName,
  });
  const [jsonInputPath, setJsonInputPath] = useState('');
  const [rules, setRules] = useState([]);
  const [formulaRules, setFormulaRules] = useState([]);
  const [databases, setDatabases] = useState([]);
  const [selectedDatabaseId, setSelectedDatabaseId] = useState('');
  const [uploadedPaths, setUploadedPaths] = useState([]);
  const [reportPaths, setReportPaths] = useState([]);
  const [status, setStatus] = useState('Ready (backend startup configuration active)');
  const [sessionResults, setSessionResults] = useState([]);
  const [selectedProfileReportPath, setSelectedProfileReportPath] = useState('');
  const [isProcessingFasta, setIsProcessingFasta] = useState(false);
  const [isProcessingVcf, setIsProcessingVcf] = useState(false);
  const [isProcessingRegenerate, setIsProcessingRegenerate] = useState(false);
  const [activeJobId, setActiveJobId] = useState('');
  const [activeJobStatus, setActiveJobStatus] = useState('');
  const [isCancelingJob, setIsCancelingJob] = useState(false);

  const [mutationFilter, setMutationFilter] = useState('');
  const [mutationFilterColumn, setMutationFilterColumn] = useState('-1');
  const [mutationSortColumn, setMutationSortColumn] = useState(null);
  const [mutationSortAsc, setMutationSortAsc] = useState(true);
  const [formulaFilter, setFormulaFilter] = useState('');
  const [formulaFilterColumn, setFormulaFilterColumn] = useState('-1');
  const [mutationColumnKeys, setMutationColumnKeys] = useState([]);
  const [formulaColumnKeys, setFormulaColumnKeys] = useState([]);
  const [mutationPlotMeta, setMutationPlotMeta] = useState({ references: [], features: [] });
  const [mutationsLoaded, setMutationsLoaded] = useState(false);
  const [activeMode, setActiveMode] = useState('profile');
  const [activeProfileMode, setActiveProfileMode] = useState('vcf');
  const [inlineReportPath, setInlineReportPath] = useState('');
  const [inlineReportLabel, setInlineReportLabel] = useState('');
  const [uploadProgress, setUploadProgress] = useState({
    percent: 0,
    fileName: '',
  });

  // --- Batch state ---
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

  // Resolve currently selected database once for all consumers.
  const selectedDatabase = databases.find((item) => item.id === selectedDatabaseId) || null;

  useEffect(() => {
    const initData = async () => {
      try {
        setStatus('Loading available databases...');
        const uiConfigPayload = await apiGet('/api/ui/config').catch(() => null);
        const uiConfig = uiConfigPayload?.data || {};
        if (Number.isFinite(uiConfig.batch_max_samples) && uiConfig.batch_max_samples > 0) {
          setBatchMaxSamples(uiConfig.batch_max_samples);
        }
        if (Number.isFinite(uiConfig.sample_limit_per_minute) && uiConfig.sample_limit_per_minute > 0) {
          setSampleLimitPerMinute(uiConfig.sample_limit_per_minute);
        }
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
        const payload = await apiGet('/api/mutations', { database_id: selectedDatabaseId });
        const items = payload.data.items || [];
        const columns = payload.data.columns || (items.length > 0 ? Object.keys(items[0]) : []);
        const formulaItems = payload.data.formula_items || [];
        const formulaColumns = payload.data.formula_columns
          || (formulaItems.length > 0 ? Object.keys(formulaItems[0]) : []);
        const plotMeta = payload.data.plot_meta || { references: [], features: [] };
        setRules(items);
        setFormulaRules(formulaItems);
        setMutationColumnKeys(columns);
        setFormulaColumnKeys(formulaColumns);
        setMutationPlotMeta(plotMeta);
        setMutationsLoaded(true);
        const databaseName = selectedDatabase ? selectedDatabase.display_name : selectedDatabaseId;
        const singleCount = Number(payload.data.single_count ?? payload.data.count ?? items.length);
        const combinationCount = Number(payload.data.formula_count ?? formulaItems.length);
        setStatus(
          `Database ${databaseName} loaded: ${singleCount} single rule(s), ${combinationCount} combination rule(s)`
        );
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
      : (rules[0] ? Object.keys(rules[0]) : []),
    mutationPlotMeta
  );

  const formulaColumns = _buildFormulaColumns(
    formulaColumnKeys.length > 0
      ? formulaColumnKeys
      : (formulaRules[0] ? Object.keys(formulaRules[0]) : []),
    mutationPlotMeta
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
    const getColValue = selectedColumn ? (selectedColumn.sortAccessor || selectedColumn.accessor) : null;
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

  const filterFormulaRules = (formulaRuleList) => {
    if (!formulaFilter) {
      return formulaRuleList;
    }

    const query = formulaFilter.toLowerCase();
    const colIdx = Number(formulaFilterColumn);

    return formulaRuleList.filter((formulaRule) => {
      if (colIdx === -1) {
        const haystack = formulaColumns.map((column) => column.accessor(formulaRule))
          .join(' ')
          .toLowerCase();
        return haystack.includes(query);
      }

      const selectedColumn = formulaColumns[colIdx];
      const cellText = selectedColumn ? selectedColumn.accessor(formulaRule) : '';
      return cellText.toLowerCase().includes(query);
    });
  };

  const displayedFormulaRules = filterFormulaRules(formulaRules);

  // Reports are shown newest first for quick access after job completion.
  const reportOptions = sessionResults
    .map((result) => ({
      path: result.report_html_path,
      jsonPath: result.report_json_path || '',
      pdfPath: result.report_pdf_path || '',
      label: `${result.sample_name} (${result.reference_name}) - ${formatResultTimestamp(result.created_at)}`,
      mode: result.mode,
    }))
    .reverse();

  const selectedReportOption = reportOptions.find((option) => option.path === selectedProfileReportPath) || null;

  useEffect(() => {
    if (!selectedProfileReportPath) {
      return;
    }
    const selectedOption = reportOptions.find((option) => option.path === selectedProfileReportPath);
    setInlineReportPath(selectedProfileReportPath);
    setInlineReportLabel(selectedOption ? selectedOption.label : 'Selected report');
  }, [reportOptions, selectedProfileReportPath]);

  const buildReportUrl = (reportPath) => {
    return buildApiUrl('/api/report', { path: reportPath });
  };

  const buildArtifactUrl = (artifactPath) => {
    return buildApiUrl('/api/artifact', { path: artifactPath });
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
      await new Promise((resolve) => setTimeout(resolve, FRONTEND_CONFIG.profile.jobPollIntervalMs));
      const payload = await apiGet(`/api/jobs/${jobId}`);
      setActiveJobStatus(payload.status);
      if (payload.status === 'succeeded') return payload.result;
      if (payload.status === 'failed') throw new Error(formatUserError(payload.error || 'Job failed'));
      if (payload.status === 'queued') {
        setStatus(`Job queued (${jobId.slice(0, 8)}...)`);
      } else {
        setStatus(`Job running (${jobId.slice(0, 8)}...)`);
      }
    }
  };

  const cancelActiveJob = async () => {
    if (!activeJobId) {
      return;
    }

    setIsCancelingJob(true);
    try {
      await apiDelete(`/api/jobs/${activeJobId}`);
      setStatus(`Job cancellation requested (${activeJobId.slice(0, 8)}...)`);
      setActiveJobStatus('canceling');
    } catch (error) {
      setStatus(formatUserError(error.message));
    } finally {
      setIsCancelingJob(false);
    }
  };

  const submitFasta = async () => {
    const databaseId = selectedDatabaseId;
    const databaseLabel = selectedDatabase ? selectedDatabase.display_name : databaseId || 'default database';
    const fastaPath = fastaInput.fasta_path;
    const fastaLabel = formatPathBasename(fastaPath);

    setIsProcessingFasta(true);
    setStatus(`Submitting FASTA profiling job for ${databaseLabel} using ${fastaLabel}...`);
    try {
      const submitResponse = await apiPost('/api/profile/fasta', {
        ...fastaInput,
        database_id: databaseId,
        threads: FRONTEND_CONFIG.profile.threads,
      });
      setActiveJobId(submitResponse.job_id);
      setActiveJobStatus('queued');
      setStatus(`Job queued (${submitResponse.job_id.slice(0, 8)}...)`);
      const result = await pollJob(submitResponse.job_id);
      // Keep a local history of run results for the report selector.
      setSessionResults((prev) => [...prev, result]);
      addResultArtifactPaths(result);
      setSelectedProfileReportPath(result.report_html_path);
      setInlineReportPath(result.report_html_path);
      setInlineReportLabel(`${result.sample_name} (${result.reference_name}) - ${formatResultTimestamp(result.created_at)}`);
      if ((result.resistance_hits || 0) === 0) {
        setStatus(
          `FASTA profiling finished for ${result.database_id || databaseLabel} using `
          + `${formatPathBasename(result.input_path || fastaPath)}. No database matches were found for this sample.`
        );
      } else {
        setStatus(
          `FASTA profiling finished for ${result.database_id || databaseLabel} using `
          + `${formatPathBasename(result.input_path || fastaPath)}. Database matches were found.`
        );
      }
    } catch (error) {
      setStatus(formatUserError(error.message));
    } finally {
      setIsProcessingFasta(false);
      setIsCancelingJob(false);
      setActiveJobId('');
      setActiveJobStatus('');
    }
  };

  const submitVcf = async () => {
    const databaseId = selectedDatabaseId;
    const databaseLabel = selectedDatabase ? selectedDatabase.display_name : databaseId || 'default database';
    const vcfPath = vcfInput.vcf_path;
    const referenceFastaPath = vcfInput.ref_fasta_path;
    const vcfLabel = formatPathBasename(vcfPath);
    const referenceLabel = formatPathBasename(referenceFastaPath);

    setIsProcessingVcf(true);
    setStatus(
      `Submitting VCF profiling job for ${databaseLabel} using ${vcfLabel} `
      + `and ${referenceLabel}...`
    );
    try {
      if (!Number.isFinite(vcfInput.min_af) || vcfInput.min_af < 0 || vcfInput.min_af > 1) {
        throw new Error('Frequency cutoff (min AF) must be a number between 0 and 1.');
      }
      if (!Number.isInteger(vcfInput.min_depth) || vcfInput.min_depth < 0) {
        throw new Error('Coverage cutoff (min depth) must be an integer greater than or equal to 0.');
      }

      const submitResponse = await apiPost('/api/profile/vcf', {
        ...vcfInput,
        database_id: databaseId,
        threads: FRONTEND_CONFIG.profile.threads,
      });
      setActiveJobId(submitResponse.job_id);
      setActiveJobStatus('queued');
      setStatus(`Job queued (${submitResponse.job_id.slice(0, 8)}...)`);
      const result = await pollJob(submitResponse.job_id);
      setSessionResults((prev) => [...prev, result]);
      addResultArtifactPaths(result);
      setSelectedProfileReportPath(result.report_html_path);
      setInlineReportPath(result.report_html_path);
      setInlineReportLabel(`${result.sample_name} (${result.reference_name}) - ${formatResultTimestamp(result.created_at)}`);
      if ((result.resistance_hits || 0) === 0) {
        setStatus(
          `VCF profiling finished for ${result.database_id || databaseLabel} using `
          + `${formatPathBasename(result.input_path || vcfPath)}. No database matches were found for this sample.`
        );
      } else {
        setStatus(
          `VCF profiling finished for ${result.database_id || databaseLabel} using `
          + `${formatPathBasename(result.input_path || vcfPath)}. Database matches were found.`
        );
      }
    } catch (error) {
      setStatus(formatUserError(error.message));
    } finally {
      setIsProcessingVcf(false);
      setIsCancelingJob(false);
      setActiveJobId('');
      setActiveJobStatus('');
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
      setStatus('Upload a valid results JSON file first.');
      return;
    }

    setIsProcessingRegenerate(true);
    setStatus('Submitting regenerate-from-JSON job...');
    try {
      const submitResponse = await apiPost('/api/regenerate/json', {
        json_path: jsonInputPath,
      });
      setActiveJobId(submitResponse.job_id);
      setActiveJobStatus('queued');
      setStatus(`Job queued (${submitResponse.job_id.slice(0, 8)}...)`);
      const result = await pollJob(submitResponse.job_id);
      setSessionResults((prev) => [...prev, result]);
      addResultArtifactPaths(result);
      setSelectedProfileReportPath(result.report_html_path);
      setInlineReportPath(result.report_html_path);
      setInlineReportLabel(`${result.sample_name} (${result.reference_name}) - ${formatResultTimestamp(result.created_at)}`);
      setStatus('JSON regeneration finished successfully.');
    } catch (error) {
      setStatus(formatUserError(error.message));
    } finally {
      setIsProcessingRegenerate(false);
      setIsCancelingJob(false);
      setActiveJobId('');
      setActiveJobStatus('');
    }
  };

  const isProfileBusy = activeProfileMode === 'fasta' ? isProcessingFasta : isProcessingVcf;

  // --- Batch functions ---

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
        const response = await fetch(`${API_BASE}/api/profile/batch/vcf`, {
          method: 'POST',
          headers: buildHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify(body),
        });
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
        const response = await fetch(`${API_BASE}/api/profile/batch/fasta`, {
          method: 'POST',
          headers: buildHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify(body),
        });
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
    const artifactPaths = batchSamples.flatMap((sample) => [
      sample.reportHtmlPath,
      sample.reportPdfPath,
      sample.reportJsonPath,
    ].filter(Boolean));

    if (artifactPaths.length === 0) {
      setBatchError('No completed batch artifacts are available for download.');
      return;
    }

    setBatchError(null);
    setIsBatchDownloadBusy(true);
    try {
      const response = await fetch(`${API_BASE}/api/artifact-bundle`, {
        method: 'POST',
        headers: buildHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ paths: artifactPaths }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(formatUserError(payload.detail || `Request failed: ${response.status}`));
      }

      const href = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = href;
      anchor.download = 'respro-batch-artifacts.zip';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(href);
    } catch (error) {
      setBatchError(formatUserError(error.message));
    } finally {
      setIsBatchDownloadBusy(false);
    }
  };

  const canCancelJob = Boolean(activeJobId) && ['queued', 'running'].includes(activeJobStatus);

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

    const blob = new Blob([`${lines.join('\n')}\n`], { type: 'application/json;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = 'results.json';
    anchor.click();
    URL.revokeObjectURL(href);
  };

  const downloadFormulaRulesAsTsv = () => {
    const headers = formulaColumns.map((column) => column.label);
    const lines = [headers.join('\t')];
    displayedFormulaRules.forEach((formulaRule) => {
      const row = formulaColumns.map((column) => {
        const raw = column.accessor(formulaRule);
        return String(raw ?? '').replace(/\t/g, ' ').replace(/\r?\n/g, ' ');
      });
      lines.push(row.join('\t'));
    });

    const blob = new Blob([`${lines.join('\n')}\n`], { type: 'text/tab-separated-values;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = 'formula-combinations.tsv';
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
    formulaRules,
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
    formulaFilter,
    setFormulaFilter,
    formulaFilterColumn,
    setFormulaFilterColumn,
    mutationsLoaded,
    activeMode,
    setActiveMode,
    activeProfileMode,
    setActiveProfileMode,
    inlineReportPath,
    inlineReportLabel,
    mutationColumns,
    formulaColumns,
    mutationPlotMeta,
    displayedRules,
    displayedFormulaRules,
    reportOptions,
    isProfileBusy,
    canCancelJob,
    isCancelingJob,
    cancelActiveJob,
    runSelectedProfile,
    openSelectedReportInline,
    buildReportUrl,
    buildArtifactUrl,
    uploadFastaFile,
    uploadVcfFile,
    uploadReferenceFile,
    uploadBamFile,
    uploadJsonFile,
    jsonInputPath,
    isRegenerateBusy: isProcessingRegenerate,
    runRegenerateFromJson,
    downloadMutationsAsTsv,
    downloadFormulaRulesAsTsv,
    uploadProgress,
    // Batch
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
    sampleLimitPerMinute,
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
