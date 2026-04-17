import { useState } from 'react';

const API_BASE = import.meta.env.VITE_RESPRO_API_BASE || 'http://127.0.0.1:8000';
const API_TOKEN = (import.meta.env.VITE_RESPRO_API_TOKEN || '').trim();

function buildHeaders(baseHeaders = {}) {
  if (!API_TOKEN) {
    return baseHeaders;
  }
  return {
    ...baseHeaders,
    Authorization: `Bearer ${API_TOKEN}`,
  };
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
    throw new Error(payload.detail || `Request failed: ${response.status}`);
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
    throw new Error(payload.detail || `Request failed: ${response.status}`);
  }
  return response.json();
}

async function apiUpload(path, file) {
  const formData = new FormData();
  formData.append('file', file);
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: buildHeaders(),
    body: formData,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Upload failed: ${response.status}`);
  }
  return response.json();
}

export function App() {
  const [vcfInput, setVcfInput] = useState({
    vcf_path: '',
    ref_fasta_path: '',
    sample: 'sample',
  });
  const [fastaInput, setFastaInput] = useState({
    fasta_path: '',
    sample: 'sample',
  });
  const [referenceFilter, setReferenceFilter] = useState('');
  const [rules, setRules] = useState([]);
  const [lastRun, setLastRun] = useState(null);
  const [status, setStatus] = useState('Ready (backend startup configuration active)');
  const [sessionResults, setSessionResults] = useState([]);

  const loadRules = async () => {
    setStatus('Loading rules...');
    const payload = await apiGet('/api/rules', {
      reference: referenceFilter.trim() || undefined,
    });
    setRules(payload.data.items);
    setStatus(`Loaded ${payload.data.count} rule(s)`);
  };

  const pollJob = async (jobId) => {
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      const payload = await apiGet(`/api/jobs/${jobId}`);
      if (payload.status === 'succeeded') return payload.result;
      if (payload.status === 'failed') throw new Error(payload.error || 'Job failed');
      setStatus(`Job running (${jobId.slice(0, 8)}...)`);
    }
  };

  const submitFasta = async () => {
    setStatus('Submitting FASTA profiling job...');
    const submitResponse = await apiPost('/api/profile/fasta', {
      ...fastaInput,
      aligner: 'pairwise',
      threads: 1,
    });
    setStatus(`Job queued (${submitResponse.job_id.slice(0, 8)}...)`);
    const result = await pollJob(submitResponse.job_id);
    setLastRun(result);
    setSessionResults((prev) => [...prev, result]);
    setStatus('FASTA profiling finished');
  };

  const submitVcf = async () => {
    setStatus('Submitting VCF profiling job...');
    const submitResponse = await apiPost('/api/profile/vcf', {
      ...vcfInput,
      aligner: 'pairwise',
      threads: 1,
      min_af: 0.01,
      min_depth: 10,
      bam_path: null,
    });
    setStatus(`Job queued (${submitResponse.job_id.slice(0, 8)}...)`);
    const result = await pollJob(submitResponse.job_id);
    setLastRun(result);
    setSessionResults((prev) => [...prev, result]);
    setStatus('VCF profiling finished');
  };

  const uploadFastaFile = async (file) => {
    setStatus(`Uploading FASTA file (${file.name})...`);
    const response = await apiUpload('/api/upload/fasta', file);
    setFastaInput((prev) => ({ ...prev, fasta_path: response.file_path }));
    setStatus(`FASTA file uploaded: ${response.file_path}`);
  };

  const uploadVcfFile = async (file) => {
    setStatus(`Uploading VCF file (${file.name})...`);
    const response = await apiUpload('/api/upload/vcf', file);
    setVcfInput((prev) => ({ ...prev, vcf_path: response.file_path }));
    setStatus(`VCF file uploaded: ${response.file_path}`);
  };

  const uploadReferenceFile = async (file) => {
    setStatus(`Uploading reference FASTA file (${file.name})...`);
    const response = await apiUpload('/api/upload/fasta', file);
    setVcfInput((prev) => ({ ...prev, ref_fasta_path: response.file_path }));
    setStatus(`Reference FASTA file uploaded: ${response.file_path}`);
  };

  return (
    <main className="layout">
      <header className="hero">
        <h1>ResistanceProfiler Web Prototype</h1>
        <p>Startup-configured local UI for FASTA/VCF profiling and resistance rules browsing.</p>
      </header>

      <section className="grid two-col">
        <article className="card">
          <h2>Profile FASTA</h2>
          <label>
            Upload FASTA file
            <input
              type="file"
              accept=".fasta,.fa,.fna,.faa"
              onChange={(event) => {
                if (event.target.files && event.target.files[0]) {
                  uploadFastaFile(event.target.files[0]).catch((error) => setStatus(error.message));
                }
              }}
            />
          </label>
          <label>
            Sample name
            <input
              value={fastaInput.sample}
              onChange={(event) => setFastaInput({ ...fastaInput, sample: event.target.value })}
            />
          </label>
          <button onClick={() => submitFasta().catch((error) => setStatus(error.message))}>
            Run FASTA Profiling
          </button>
        </article>

        <article className="card">
          <h2>Profile VCF</h2>
          <label>
            Upload VCF file
            <input
              type="file"
              accept=".vcf,.vcf.gz"
              onChange={(event) => {
                if (event.target.files && event.target.files[0]) {
                  uploadVcfFile(event.target.files[0]).catch((error) => setStatus(error.message));
                }
              }}
            />
          </label>
          <label>
            Upload reference FASTA file
            <input
              type="file"
              accept=".fasta,.fa,.fna"
              onChange={(event) => {
                if (event.target.files && event.target.files[0]) {
                  uploadReferenceFile(event.target.files[0]).catch((error) => setStatus(error.message));
                }
              }}
            />
          </label>
          <label>
            Sample name
            <input
              value={vcfInput.sample}
              onChange={(event) => setVcfInput({ ...vcfInput, sample: event.target.value })}
            />
          </label>
          <button onClick={() => submitVcf().catch((error) => setStatus(error.message))}>
            Run VCF Profiling
          </button>
        </article>
      </section>

      <section className="card">
        <h2>Rules Browser</h2>
        <label>
          Optional reference filter
          <input
            value={referenceFilter}
            onChange={(event) => setReferenceFilter(event.target.value)}
            placeholder="HSV1"
          />
        </label>
        <button onClick={() => loadRules().catch((error) => setStatus(error.message))}>Load Rules</button>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Reference</th>
                <th>Gene</th>
                <th>Pos</th>
                <th>Mutation</th>
                <th>Drug</th>
                <th>Phenotype</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule, index) => (
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
      </section>

      {sessionResults.length > 0 && (
        <section className="card">
          <h2>Session Results</h2>
          <div className="results-grid">
            {sessionResults.map((result, index) => {
              const params = new URLSearchParams({ path: result.report_html_path });
              if (API_TOKEN) {
                params.set('token', API_TOKEN);
              }
              const url = `${API_BASE}/api/report?${params.toString()}`;
              return (
                <div key={index} className="result-item">
                  <p>{result.sample_name}</p>
                  <a className="button-link" href={url} target="_blank" rel="noreferrer">
                    Open Report
                  </a>
                </div>
              );
            })}
          </div>
        </section>
      )}

      <footer className="status">{status}</footer>
    </main>
  );
}
