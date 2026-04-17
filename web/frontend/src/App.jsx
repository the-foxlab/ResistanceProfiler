import { useEffect, useRef, useState } from 'react';

const API_BASE = import.meta.env.VITE_RESPRO_API_BASE || 'http://127.0.0.1:8000';
const API_TOKEN = (import.meta.env.VITE_RESPRO_API_TOKEN || '').trim();

// Simple spinner SVG component
function Spinner() {
  return (
    <svg className="spinner" viewBox="0 0 50 50" xmlns="http://www.w3.org/2000/svg">
      <circle cx="25" cy="25" r="20" fill="none" stroke="currentColor" strokeWidth="4" opacity="0.3" />
      <circle cx="25" cy="25" r="20" fill="none" stroke="currentColor" strokeWidth="4" strokeDasharray="30" />
    </svg>
  );
}

function buildHeaders(baseHeaders = {}) {
  if (!API_TOKEN) {
    return baseHeaders;
  }
  return {
    ...baseHeaders,
    Authorization: `Bearer ${API_TOKEN}`,
  };
}

function formatUserError(message) {
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
  const filteredParams = Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== '')
  );
  const query = new URLSearchParams(filteredParams);
  const response = await fetch(`${API_BASE}${path}?${query.toString()}`, {
    headers: buildHeaders(),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(formatUserError(payload.detail || `Request failed: ${response.status}`));
  }
  return response.json();
}

async function apiPost(path, body) {
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

async function uploadWithProgress(file, path, onProgress) {
  const formData = new FormData();
  formData.append('file', file);
  
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    // Track upload progress
    if (xhr.upload) {
      xhr.upload.addEventListener('progress', (event) => {
        if (event.lengthComputable) {
          const percentComplete = (event.loaded / event.total) * 100;
          onProgress(percentComplete);
        }
      });
    }

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const response = JSON.parse(xhr.responseText);
          resolve(response);
        } catch (error) {
          reject(new Error(formatUserError('Failed to parse upload response')));
        }
      } else {
        try {
          const error = JSON.parse(xhr.responseText);
          reject(new Error(formatUserError(error.detail || `Upload failed: ${xhr.status}`)));
        } catch {
          reject(new Error(formatUserError(`Upload failed: ${xhr.status}`)));
        }
      }
    });

    xhr.addEventListener('error', () => {
      reject(new Error(formatUserError('Upload failed: network error')));
    });

    xhr.addEventListener('abort', () => {
      reject(new Error(formatUserError('Upload cancelled')));
    });

    xhr.open('POST', `${API_BASE}${path}`);
    if (API_TOKEN) {
      xhr.setRequestHeader('Authorization', `Bearer ${API_TOKEN}`);
    }
    xhr.send(formData);
  });
}

export function App() {
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
  const [selectedFastaReportPath, setSelectedFastaReportPath] = useState('');
  const [selectedVcfReportPath, setSelectedVcfReportPath] = useState('');
  const [isProcessingFasta, setIsProcessingFasta] = useState(false);
  const [isProcessingVcf, setIsProcessingVcf] = useState(false);
  const [uploadProgress, setUploadProgress] = useState({ fasta: null, vcf: null, bam: null });
  
  // Mutation table filtering and sorting
  const [mutationFilter, setMutationFilter] = useState('');
  const [mutationFilterColumn, setMutationFilterColumn] = useState('-1');
  const [mutationSortColumn, setMutationSortColumn] = useState(null);
  const [mutationSortAsc, setMutationSortAsc] = useState(true);
  const [mutationsLoaded, setMutationsLoaded] = useState(false);
  
  const fileInputRefs = {
    fasta: useRef(null),
    vcf: useRef(null),
    refFasta: useRef(null),
    bam: useRef(null),
  };

  const selectedDatabase = databases.find((item) => item.id === selectedDatabaseId) || null;

  // Auto-load databases on mount
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

  // Reload mutations whenever selected database changes
  useEffect(() => {
    if (!selectedDatabaseId) {
      return;
    }
    const loadMutationsForDb = async () => {
      setStatus('Loading mutations from database...');
      try {
        const payload = await apiGet('/api/mutations');
        setRules(payload.data.items);
        setMutationsLoaded(true);
        setStatus(`Loaded. Ready to browse ${payload.data.count} mutation(s).`);
      } catch (error) {
        setStatus(`Error loading mutations: ${error.message}`);
      }
    };
    loadMutationsForDb();
  }, [selectedDatabaseId]);



  const parseValue = (text) => {
    const raw = (text || '').trim();
    const num = Number(raw);
    if (!Number.isNaN(num) && raw !== '') {
      return { kind: 'num', value: num };
    }
    return { kind: 'txt', value: raw.toLowerCase() };
  };

  const filterMutations = (rules) => {
    if (!mutationFilter) {
      return rules;
    }
    const query = mutationFilter.toLowerCase();
    const colIdx = Number(mutationFilterColumn);
    
    return rules.filter((rule) => {
      if (colIdx === -1) {
        // Search all columns
        const haystack = [
          rule.reference_name,
          rule.gene,
          String(rule.position),
          `${rule.reference}${rule.position + 1}${rule.mutation}`,
          rule.drug,
          rule.phenotype,
        ]
          .join(' ')
          .toLowerCase();
        return haystack.includes(query);
      } else {
        // Search specific column
        const columns = [
          rule.reference_name,
          rule.gene,
          String(rule.position),
          `${rule.reference}${rule.position + 1}${rule.mutation}`,
          rule.drug,
          rule.phenotype,
        ];
        const cellText = columns[colIdx] || '';
        return cellText.toLowerCase().includes(query);
      }
    });
  };

  const sortMutations = (rulesToSort) => {
    if (mutationSortColumn === null) {
      return rulesToSort;
    }

    const columns = [
      (rule) => rule.reference_name,
      (rule) => rule.gene,
      (rule) => rule.position,
      (rule) => `${rule.reference}${rule.position + 1}${rule.mutation}`,
      (rule) => rule.drug,
      (rule) => rule.phenotype,
    ];

    const getColValue = columns[mutationSortColumn];
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

  const buildReportUrl = (reportPath) => {
    const params = new URLSearchParams({ path: reportPath });
    if (API_TOKEN) {
      params.set('token', API_TOKEN);
    }
    return `${API_BASE}/api/report?${params.toString()}`;
  };

  const addUploadedPath = (path) => {
    setUploadedPaths((prev) => {
      if (prev.includes(path)) {
        return prev;
      }
      return [...prev, path];
    });
  };

  const addReportPath = (path) => {
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

  const pollJob = async (jobId) => {
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
      setSessionResults((prev) => [...prev, result]);
      addReportPath(result.report_html_path);
      setSelectedFastaReportPath(result.report_html_path);
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
      setSelectedVcfReportPath(result.report_html_path);
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
    setStatus(`Uploading ${fileType.toUpperCase()} file (${file.name})...`);
    setUploadProgress((prev) => ({ ...prev, [fileType]: 0 }));
    try {
      const response = await uploadWithProgress(file, `/api/upload/${fileType}`, (progress) => {
        setUploadProgress((prev) => ({ ...prev, [fileType]: progress }));
      });
      onSuccess(response.file_path);
      addUploadedPath(response.file_path);
      setUploadProgress((prev) => ({ ...prev, [fileType]: null }));
      setStatus(`${fileType.toUpperCase()} file uploaded: ${response.file_path}`);
    } catch (error) {
      setUploadProgress((prev) => ({ ...prev, [fileType]: null }));
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

  const fastaResults = sessionResults.filter((result) => result.mode === 'fasta');
  const vcfResults = sessionResults.filter((result) => result.mode === 'vcf');

  return (
    <main className="layout">
      <header className="hero">
        <h1>ResistanceProfiler Web Prototype</h1>
        <p>Startup-configured local UI for FASTA/VCF profiling and mutation browsing.</p>
      </header>

      <section className="grid two-col">
        <article className="card">
          <h2>Profile FASTA</h2>
          <label>
            Upload FASTA file
            <input
              type="file"
              accept=".fasta,.fa,.fna,.faa"
              ref={fileInputRefs.fasta}
              onChange={(event) => {
                if (event.target.files && event.target.files[0]) {
                  uploadFastaFile(event.target.files[0]).catch((error) => setStatus(error.message));
                }
              }}
            />
          </label>
          {uploadProgress.fasta !== null && (
            <div className="progress-bar-container">
              <div className="progress-bar" style={{ width: `${uploadProgress.fasta}%` }} />
              <span className="progress-text">{Math.round(uploadProgress.fasta)}%</span>
            </div>
          )}
          <label>
            Sample name
            <input
              value={fastaInput.sample}
              onChange={(event) => setFastaInput({ ...fastaInput, sample: event.target.value })}
            />
          </label>
          <button
            onClick={() => submitFasta().catch((error) => setStatus(error.message))}
            disabled={isProcessingFasta}
          >
            {isProcessingFasta ? (
              <>
                <Spinner /> Running FASTA Profiling
              </>
            ) : (
              'Run FASTA Profiling'
            )}
          </button>
          <div className="inline-actions">
            <select
              value={selectedFastaReportPath}
              onChange={(event) => setSelectedFastaReportPath(event.target.value)}
              disabled={fastaResults.length === 0}
            >
              <option value="">Session FASTA results</option>
              {fastaResults.map((result) => (
                <option key={result.report_html_path} value={result.report_html_path}>
                  {result.sample_name} ({result.reference_name}) - {formatResultTimestamp(result.created_at)}
                </option>
              ))}
            </select>
            <a
              className="button-link"
              href={selectedFastaReportPath ? buildReportUrl(selectedFastaReportPath) : '#'}
              target="_blank"
              rel="noreferrer"
              onClick={(event) => {
                if (!selectedFastaReportPath) {
                  event.preventDefault();
                }
              }}
            >
              Open Selected
            </a>
          </div>
        </article>

        <article className="card">
          <h2>Profile VCF</h2>
          <label>
            Upload VCF file
            <input
              type="file"
              accept=".vcf,.vcf.gz"
              ref={fileInputRefs.vcf}
              onChange={(event) => {
                if (event.target.files && event.target.files[0]) {
                  uploadVcfFile(event.target.files[0]).catch((error) => setStatus(error.message));
                }
              }}
            />
          </label>
          {uploadProgress.vcf !== null && (
            <div className="progress-bar-container">
              <div className="progress-bar" style={{ width: `${uploadProgress.vcf}%` }} />
              <span className="progress-text">{Math.round(uploadProgress.vcf)}%</span>
            </div>
          )}
          <label>
            Upload reference FASTA file
            <input
              type="file"
              accept=".fasta,.fa,.fna"
              ref={fileInputRefs.refFasta}
              onChange={(event) => {
                if (event.target.files && event.target.files[0]) {
                  uploadReferenceFile(event.target.files[0]).catch((error) => setStatus(error.message));
                }
              }}
            />
          </label>
          <label>
            Upload BAM file (optional, for coverage calculations)
            <input
              type="file"
              accept=".bam"
              ref={fileInputRefs.bam}
              onChange={(event) => {
                if (event.target.files && event.target.files[0]) {
                  uploadBamFile(event.target.files[0]).catch((error) => setStatus(error.message));
                }
              }}
            />
          </label>
          <p className="field-hint">BAI is generated automatically on the server. The BAM must be coordinate-sorted.</p>
          {uploadProgress.bam !== null && (
            <div className="progress-bar-container">
              <div className="progress-bar" style={{ width: `${uploadProgress.bam}%` }} />
              <span className="progress-text">{Math.round(uploadProgress.bam)}%</span>
            </div>
          )}
          <label>
            Sample name
            <input
              value={vcfInput.sample}
              onChange={(event) => setVcfInput({ ...vcfInput, sample: event.target.value })}
            />
          </label>
          <button
            onClick={() => submitVcf().catch((error) => setStatus(error.message))}
            disabled={isProcessingVcf}
          >
            {isProcessingVcf ? (
              <>
                <Spinner /> Running VCF Profiling
              </>
            ) : (
              'Run VCF Profiling'
            )}
          </button>
          <div className="inline-actions">
            <select
              value={selectedVcfReportPath}
              onChange={(event) => setSelectedVcfReportPath(event.target.value)}
              disabled={vcfResults.length === 0}
            >
              <option value="">Session VCF results</option>
              {vcfResults.map((result) => (
                <option key={result.report_html_path} value={result.report_html_path}>
                  {result.sample_name} ({result.reference_name}) - {formatResultTimestamp(result.created_at)}
                </option>
              ))}
            </select>
            <a
              className="button-link"
              href={selectedVcfReportPath ? buildReportUrl(selectedVcfReportPath) : '#'}
              target="_blank"
              rel="noreferrer"
              onClick={(event) => {
                if (!selectedVcfReportPath) {
                  event.preventDefault();
                }
              }}
            >
              Open Selected
            </a>
          </div>
        </article>
      </section>

      <section className="card">
        <h2>Browse Mutations In Database</h2>
        {databases.length > 0 && (
          <div className="inline-actions" style={{ marginBottom: '0.5rem' }}>
            <select
              value={selectedDatabaseId}
              onChange={(event) => setSelectedDatabaseId(event.target.value)}
            >
              {databases.map((database) => (
                <option key={database.id} value={database.id}>{database.display_name}</option>
              ))}
            </select>
          </div>
        )}
        {selectedDatabase ? (
          <div className="database-meta">
            <p><strong>Created:</strong> {selectedDatabase.created_at || 'n/a'}</p>
            <p><strong>Supported organisms:</strong> {selectedDatabase.supported_organisms.join(', ') || 'n/a'}</p>
            <p><strong>Mutations in database:</strong> {selectedDatabase.mutation_count}</p>
          </div>
        ) : null}
        
        <div className="table-controls-container">
          <div className="table-controls">
            <label>Filter:</label>
            <select
              value={mutationFilterColumn}
              onChange={(event) => setMutationFilterColumn(event.target.value)}
            >
              <option value="-1">All columns</option>
              <option value="0">Reference</option>
              <option value="1">Gene</option>
              <option value="2">Position</option>
              <option value="3">Mutation</option>
              <option value="4">Drug</option>
              <option value="5">Phenotype</option>
            </select>
            <input
              type="text"
              placeholder="contains..."
              value={mutationFilter}
              onChange={(event) => setMutationFilter(event.target.value)}
            />
            <button
              type="button"
              onClick={() => {
                setMutationFilter('');
                setMutationFilterColumn('-1');
              }}
            >
              Reset
            </button>
          </div>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th
                  className="sortable-col"
                  data-order={mutationSortColumn === 0 ? (mutationSortAsc ? 'asc' : 'desc') : null}
                  onClick={() => {
                    if (mutationSortColumn === 0) {
                      setMutationSortAsc(!mutationSortAsc);
                    } else {
                      setMutationSortColumn(0);
                      setMutationSortAsc(true);
                    }
                  }}
                >
                  Reference
                </th>
                <th
                  className="sortable-col"
                  data-order={mutationSortColumn === 1 ? (mutationSortAsc ? 'asc' : 'desc') : null}
                  onClick={() => {
                    if (mutationSortColumn === 1) {
                      setMutationSortAsc(!mutationSortAsc);
                    } else {
                      setMutationSortColumn(1);
                      setMutationSortAsc(true);
                    }
                  }}
                >
                  Gene
                </th>
                <th
                  className="sortable-col"
                  data-order={mutationSortColumn === 2 ? (mutationSortAsc ? 'asc' : 'desc') : null}
                  onClick={() => {
                    if (mutationSortColumn === 2) {
                      setMutationSortAsc(!mutationSortAsc);
                    } else {
                      setMutationSortColumn(2);
                      setMutationSortAsc(true);
                    }
                  }}
                >
                  Pos
                </th>
                <th
                  className="sortable-col"
                  data-order={mutationSortColumn === 3 ? (mutationSortAsc ? 'asc' : 'desc') : null}
                  onClick={() => {
                    if (mutationSortColumn === 3) {
                      setMutationSortAsc(!mutationSortAsc);
                    } else {
                      setMutationSortColumn(3);
                      setMutationSortAsc(true);
                    }
                  }}
                >
                  Mutation
                </th>
                <th
                  className="sortable-col"
                  data-order={mutationSortColumn === 4 ? (mutationSortAsc ? 'asc' : 'desc') : null}
                  onClick={() => {
                    if (mutationSortColumn === 4) {
                      setMutationSortAsc(!mutationSortAsc);
                    } else {
                      setMutationSortColumn(4);
                      setMutationSortAsc(true);
                    }
                  }}
                >
                  Drug
                </th>
                <th
                  className="sortable-col"
                  data-order={mutationSortColumn === 5 ? (mutationSortAsc ? 'asc' : 'desc') : null}
                  onClick={() => {
                    if (mutationSortColumn === 5) {
                      setMutationSortAsc(!mutationSortAsc);
                    } else {
                      setMutationSortColumn(5);
                      setMutationSortAsc(true);
                    }
                  }}
                >
                  Phenotype
                </th>
              </tr>
            </thead>
            <tbody>
              {displayedRules.map((rule, index) => (
                <tr key={`${rule.reference_name}-${rule.gene}-${rule.position}-${index}`}>
                  <td>{rule.reference_name}</td>
                  <td>{rule.gene}</td>
                  <td>{rule.position + 1}</td>
                  <td>{rule.reference}{rule.position + 1}{rule.mutation}</td>
                  <td>{rule.drug}</td>
                  <td>{rule.phenotype}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {mutationsLoaded && rules.length === 0 ? (
          <p className="status">No mutations were found for the selected database/filter.</p>
        ) : null}
        {mutationsLoaded && rules.length > 0 && displayedRules.length === 0 ? (
          <p className="status">No mutations match the current filter.</p>
        ) : null}
      </section>

      <footer className="status">{status}</footer>
    </main>
  );
}
