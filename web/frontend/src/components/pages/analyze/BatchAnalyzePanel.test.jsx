import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';

import { BatchAnalyzePanel } from './BatchAnalyzePanel';
import { useBatchManager } from '../../../hooks/useBatchManager';

function makePanelLogic(overrides = {}) {
  return {
    selectedDatabase: { id: 'db1', display_name: 'HSV-1 DB' },
    buildReportUrl: (id) => `/api/report?artifact_id=${id}`,
    buildArtifactUrl: (id) => `/api/artifact?artifact_id=${id}`,
    batchMode: 'vcf',
    batchVcfFiles: [],
    batchFastaFiles: [],
    batchJsonFiles: [],
    batchReferenceFasta: null,
    batchSamples: [],
    isBatchUploading: false,
    batchSubmitting: false,
    isBatchPolling: false,
    isBatchDownloadBusy: false,
    batchError: null,
    batchRateLimitCooldown: 0,
    batchSubmitted: false,
    batchMaxSamples: 25,
    batchVcfCutoffs: { min_af: 0.01, min_depth: 10 },
    setBatchVcfCutoffs: vi.fn(),
    addBatchVcfFiles: vi.fn(),
    addBatchFastaFiles: vi.fn(),
    addBatchJsonFiles: vi.fn(),
    addBatchBamFiles: vi.fn(),
    attachBatchBam: vi.fn(),
    removeBatchFile: vi.fn(),
    uploadBatchReferenceFasta: vi.fn(),
    submitBatch: vi.fn(),
    stopBatchPolling: vi.fn(),
    downloadAllBatchArtifacts: vi.fn(),
    resetBatch: vi.fn(),
    ...overrides,
  };
}

describe('BatchAnalyzePanel', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('renders VCF, FASTA, and JSON rows with limit indicator and shared reference status', () => {
    const vcfRows = [
      { rowId: 'row-1', uploadId: 'up-1', name: 's1.vcf', size: 2048, bamId: 'bam-1', bamName: 's1.bam', bamSize: 4096 },
    ];
    const { rerender } = render(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchMode: 'vcf',
          batchVcfFiles: vcfRows,
          batchMaxSamples: 1,
          batchReferenceFasta: { uploadId: 'ref-1', name: 'ref.fasta' },
        })}
      />,
    );

    expect(screen.getByText('s1.vcf')).toBeInTheDocument();
    expect(screen.getByText('2 KB')).toBeInTheDocument();
    expect(screen.getByText('s1.bam')).toBeInTheDocument();
    expect(screen.getByText('4 KB')).toBeInTheDocument();
    expect(screen.getByText(/1 \/ 1 files/)).toBeInTheDocument();
    expect(screen.getByText('Limit reached')).toBeInTheDocument();
    expect(screen.getByText('Uploaded reference: ref.fasta')).toBeInTheDocument();
    expect(screen.getByLabelText('Replace BAM for s1.vcf')).toBeInTheDocument();

    rerender(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchMode: 'fasta',
          batchFastaFiles: [{ rowId: 'row-f1', uploadId: 'up-f1', name: 's1.fasta', size: 1024 }],
          batchReferenceFasta: { uploadId: 'ref-1', name: 'ref.fasta' },
        })}
      />,
    );
    expect(screen.getByText('FASTA file')).toBeInTheDocument();
    expect(screen.queryByText('Uploaded reference: ref.fasta')).not.toBeInTheDocument();

    rerender(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchMode: 'json',
          batchJsonFiles: [{ rowId: 'row-j1', uploadId: 'up-j1', name: 's1.json', size: 3072 }],
        })}
      />,
    );
    expect(screen.getByText('JSON file')).toBeInTheDocument();
    expect(screen.getByText('s1.json')).toBeInTheDocument();
  });

  it('passes immutable rowId (never array index) to attachBatchBam and removeBatchFile', () => {
    const attachBatchBam = vi.fn();
    const removeBatchFile = vi.fn();
    const row1 = { rowId: 'row-101', uploadId: 'up-1', name: 's1.vcf', size: 1024, bamId: null, bamName: null, bamSize: null };
    const row2 = { rowId: 'row-102', uploadId: 'up-2', name: 's2.vcf', size: 1024, bamId: null, bamName: null, bamSize: null };

    const { rerender } = render(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchVcfFiles: [row1, row2],
          attachBatchBam,
          removeBatchFile,
        })}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Remove s1.vcf' }));
    expect(removeBatchFile).toHaveBeenCalledWith('row-101');

    // Rerender with row 2 now at array index 0; attachBatchBam must still receive 'row-102'.
    rerender(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchVcfFiles: [row2],
          attachBatchBam,
          removeBatchFile,
        })}
      />,
    );
    const bamInput = screen.getByLabelText('Attach BAM to s2.vcf');
    const bamFile = new File(['BAM'], 's2.bam', { type: 'application/octet-stream' });
    fireEvent.change(bamInput, { target: { files: [bamFile] } });
    expect(attachBatchBam).toHaveBeenCalledWith('row-102', bamFile);
  });

  it('W1/WA-007 integration: async BAM upload targets row 2 after row 1 is deleted, and ignores deleted target row', async () => {
    const xhrInstances = [];
    vi.stubGlobal('XMLHttpRequest', class {
      constructor() {
        this.open = vi.fn();
        this.send = vi.fn();
        this.abort = vi.fn(() => this.onabort?.({}));
        this.upload = { onprogress: null };
        this.status = 200;
        this.responseText = '';
        xhrInstances.push(this);
      }
      triggerSuccess(payload) {
        this.status = 200;
        this.responseText = JSON.stringify(payload);
        this.onload?.({});
      }
    });

    function Harness() {
      const [, setProgress] = useState({ percent: 0, fileName: '' });
      const [, setResults] = useState([]);
      const batch = useBatchManager({
        selectedDatabaseId: 'db1',
        addUploadedPath: vi.fn(),
        addResultArtifactPaths: vi.fn(),
        setSessionResults: setResults,
        setUploadProgress: setProgress,
      });
      return (
        <BatchAnalyzePanel
          logic={{
            selectedDatabase: null,
            buildReportUrl: (id) => `/api/report?artifact_id=${id}`,
            buildArtifactUrl: (id) => `/api/artifact?artifact_id=${id}`,
            ...batch,
          }}
        />
      );
    }

    render(<Harness />);
    const vcfInput = document.querySelector('input[accept=".vcf,.vcf.gz"]');
    const f1 = new File(['##VCF'], 'sample1.vcf');
    const f2 = new File(['##VCF'], 'sample2.vcf');

    await act(async () => {
      fireEvent.change(vcfInput, { target: { files: [f1, f2] } });
      await Promise.resolve();
      xhrInstances[0].triggerSuccess({ upload_id: 'up-vcf-1' });
      await Promise.resolve();
      await new Promise((r) => setTimeout(r, 0));
      xhrInstances[1].triggerSuccess({ upload_id: 'up-vcf-2' });
    });

    await waitFor(() => {
      expect(screen.getByText('sample1.vcf')).toBeInTheDocument();
      expect(screen.getByText('sample2.vcf')).toBeInTheDocument();
    });

    // Start async BAM upload on row 2, then remove row 1 before XHR completes.
    const row2BamInput = screen.getByLabelText('Attach BAM to sample2.vcf');
    fireEvent.change(row2BamInput, { target: { files: [new File(['BAM'], 'row2.bam')] } });
    fireEvent.click(screen.getByRole('button', { name: 'Remove sample1.vcf' }));

    await act(async () => {
      xhrInstances[2].triggerSuccess({ upload_id: 'up-bam-2' });
    });

    expect(screen.queryByText('sample1.vcf')).not.toBeInTheDocument();
    expect(screen.getByText('row2.bam')).toBeInTheDocument();

    // Start async BAM upload on row 2, then delete row 2 before XHR resolves -> remaining UI shows empty state.
    fireEvent.change(screen.getByLabelText('Replace BAM for sample2.vcf'), {
      target: { files: [new File(['BAM'], 'late.bam')] },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Remove sample2.vcf' }));
    await act(async () => {
      xhrInstances[3].triggerSuccess({ upload_id: 'up-bam-late' });
    });
    expect(screen.queryByText('late.bam')).not.toBeInTheDocument();
  });

  it('resets shared reference FASTA input value so re-selecting the same file after reset works', () => {
    const uploadBatchReferenceFasta = vi.fn();
    render(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchMode: 'vcf',
          uploadBatchReferenceFasta,
        })}
      />,
    );

    const refInput = document.querySelector('input[accept=".fasta,.fa,.fna"]');
    const refFile = new File(['>ref\nACGT'], 'ref.fasta');
    fireEvent.change(refInput, { target: { files: [refFile] } });

    expect(uploadBatchReferenceFasta).toHaveBeenCalledWith(refFile);
    expect(refInput.value).toBe('');
  });

  it('resets multi-file input value synchronously before addBatchVcfFiles resolves', async () => {
    let resolveAdd;
    const addBatchVcfFiles = vi.fn(
      () => new Promise((resolve) => {
        resolveAdd = resolve;
      }),
    );
    render(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchMode: 'vcf',
          addBatchVcfFiles,
        })}
      />,
    );

    const vcfInput = document.querySelector('input[accept=".vcf,.vcf.gz"]');
    const f1 = new File(['##VCF'], 's1.vcf');
    const f2 = new File(['##VCF'], 's2.vcf');
    fireEvent.change(vcfInput, { target: { files: [f1, f2] } });

    expect(vcfInput.value).toBe('');
    expect(addBatchVcfFiles).toHaveBeenCalledWith([f1, f2]);

    await act(async () => {
      resolveAdd();
    });
  });

  it('gates Submit batch on uploads/reference/cooldown/busy and displays submitted polling & stop states', () => {
    const submitBatch = vi.fn();
    const stopBatchPolling = vi.fn();
    const downloadAllBatchArtifacts = vi.fn();
    const resetBatch = vi.fn();

    const { rerender } = render(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchMode: 'vcf',
          batchVcfFiles: [{ rowId: 'r1', uploadId: 'u1', name: 'a.vcf', size: 100 }],
          batchReferenceFasta: null,
          batchRateLimitCooldown: 12,
          batchError: 'Sample error',
          submitBatch,
        })}
      />,
    );

    expect(screen.getByRole('button', { name: 'Submit batch' })).toBeDisabled();
    expect(screen.getByText('Rate limit reached. Try again in 12s.')).toBeInTheDocument();
    expect(screen.getByText('Sample error')).toBeInTheDocument();

    rerender(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchSubmitted: true,
          isBatchPolling: true,
          batchSubmitting: true,
          batchRateLimitCooldown: 9,
          batchSamples: [
            { job_id: 'j1', sample_name: 's1', status: 'running', errorMessage: null },
            { job_id: 'j2', sample_name: 's2', status: 'failed', errorMessage: 'Job not found.' },
          ],
          stopBatchPolling,
          downloadAllBatchArtifacts,
          resetBatch,
        })}
      />,
    );

    expect(screen.getByText('Rate limit reached. Retrying in 9s.')).toBeInTheDocument();
    expect(screen.getByText(/Polling batch status/)).toBeInTheDocument();
    expect(screen.getByText('Job not found.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Stop polling' }));
    expect(stopBatchPolling).toHaveBeenCalledTimes(1);

    rerender(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchSubmitted: true,
          isBatchPolling: false,
          batchSubmitting: false,
          batchError: null,
          batchSamples: [
            { job_id: 'j1', sample_name: 's1', status: 'queued', errorMessage: null },
            {
              job_id: 'j2',
              sample_name: 's2',
              status: 'succeeded',
              errorMessage: null,
              reportHtmlPath: 'h2',
              artifactIds: ['h2'],
            },
          ],
          downloadAllBatchArtifacts,
          resetBatch,
        })}
      />,
    );

    expect(screen.queryByRole('button', { name: 'Stop polling' })).not.toBeInTheDocument();
    expect(screen.getByText(/Batch status polling stopped/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Download all' }));
    expect(downloadAllBatchArtifacts).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'New batch' }));
    expect(resetBatch).toHaveBeenCalledTimes(1);
  });

  it('announces batchError with role="alert" and --color-error pre- and post-submit while keeping cooldown status non-alert (WA-053/WA-027)', () => {
    const { rerender } = render(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchSubmitted: false,
          batchVcfFiles: [{ rowId: 'r1', uploadId: 'u1', name: 'a.vcf', size: 100 }],
          batchMaxSamples: 1,
          batchRateLimitCooldown: 8,
          batchError: 'Pre-submit failure',
        })}
      />,
    );

    const limitLabel = screen.getByText('Limit reached');
    expect(limitLabel).toHaveStyle({ color: 'var(--color-error)' });

    const preAlert = screen.getByRole('alert');
    expect(preAlert).toHaveTextContent('Pre-submit failure');
    expect(preAlert).toHaveStyle({ color: 'var(--color-error)' });
    expect(screen.getByText('Rate limit reached. Try again in 8s.')).not.toHaveAttribute('role', 'alert');

    rerender(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchSubmitted: true,
          batchRateLimitCooldown: 5,
          batchError: 'Post-submit polling error',
          batchSamples: [{ job_id: 'j1', sample_name: 's1', status: 'running', errorMessage: null }],
        })}
      />,
    );

    const postAlert = screen.getByRole('alert');
    expect(postAlert).toHaveTextContent('Post-submit polling error');
    expect(postAlert).toHaveStyle({ color: 'var(--color-error)' });
    expect(screen.getByText('Rate limit reached. Retrying in 5s.')).not.toHaveAttribute('role', 'alert');
  });

  it('exposes accessible names on batch file inputs, Clear all, per-row controls, and InfoButtons', () => {
    const { rerender } = render(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchMode: 'vcf',
          batchVcfFiles: [{ rowId: 'r1', uploadId: 'u1', name: 's1.vcf', size: 1024, bamName: null }],
        })}
      />,
    );

    expect(screen.getByLabelText(/VCF files/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Shared reference FASTA/)).toBeInTheDocument();
    expect(screen.getByLabelText(/BAM files/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Clear all uploaded batch files' })).toBeInTheDocument();
    expect(screen.getByLabelText('Attach BAM to s1.vcf')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove s1.vcf' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Batch VCF help' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Batch reference help' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Batch BAM help' })).toBeInTheDocument();

    rerender(<BatchAnalyzePanel logic={makePanelLogic({ batchMode: 'fasta' })} />);
    expect(screen.getByLabelText(/FASTA files/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Batch FASTA help' })).toBeInTheDocument();

    rerender(<BatchAnalyzePanel logic={makePanelLogic({ batchMode: 'json' })} />);
    expect(screen.getByLabelText(/Results JSON files/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Batch JSON help' })).toBeInTheDocument();
  });

  it('disables all pre-submit inputs, row actions, Clear all, and hides database hint while batchSubmitting is true', () => {
    const { rerender } = render(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchMode: 'vcf',
          batchSubmitting: true,
          batchVcfFiles: [{ rowId: 'r1', uploadId: 'u1', name: 's1.vcf', size: 1024, bamName: null }],
          batchReferenceFasta: { uploadId: 'ref-1', name: 'ref.fasta' },
        })}
      />,
    );

    expect(screen.getByLabelText(/VCF files/, { selector: 'input' })).toBeDisabled();
    expect(screen.getByLabelText(/Shared reference FASTA/, { selector: 'input' })).toBeDisabled();
    expect(screen.getByLabelText(/BAM files/, { selector: 'input' })).toBeDisabled();
    expect(screen.getByLabelText('Attach BAM to s1.vcf')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Remove s1.vcf' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Clear all uploaded batch files' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /submitting/i })).toBeDisabled();
    expect(screen.queryByText(/using HSV-1 DB/)).not.toBeInTheDocument();

    for (const input of document.querySelectorAll('.batch-advanced-options input')) {
      expect(input).toBeDisabled();
    }

    rerender(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchMode: 'fasta',
          batchSubmitting: true,
          batchFastaFiles: [{ rowId: 'r2', uploadId: 'u2', name: 's1.fasta', size: 1024 }],
        })}
      />,
    );
    expect(screen.getByLabelText(/FASTA files/, { selector: 'input' })).toBeDisabled();

    rerender(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchMode: 'json',
          batchSubmitting: true,
          batchJsonFiles: [{ rowId: 'r3', uploadId: 'u3', name: 's1.json', size: 1024 }],
        })}
      />,
    );
    expect(screen.getByLabelText(/Results JSON files/, { selector: 'input' })).toBeDisabled();
  });

  it('updates batchVcfCutoffs via functional updaters and ignores empty multi-file changes', () => {
    const setBatchVcfCutoffs = vi.fn();
    const addBatchVcfFiles = vi.fn();
    render(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchMode: 'vcf',
          batchVcfCutoffs: { min_af: 0.05, min_depth: 15 },
          setBatchVcfCutoffs,
          addBatchVcfFiles,
        })}
      />,
    );

    fireEvent.change(screen.getByLabelText(/VCF files/, { selector: 'input' }), { target: { files: [] } });
    expect(addBatchVcfFiles).not.toHaveBeenCalled();

    const inputs = document.querySelectorAll('.batch-advanced-options input[type="number"]');
    expect(inputs).toHaveLength(2);
    fireEvent.change(inputs[0], { target: { value: '0.2' } });
    fireEvent.change(inputs[1], { target: { value: '30' } });

    expect(setBatchVcfCutoffs).toHaveBeenCalledTimes(2);
    const firstUpdater = setBatchVcfCutoffs.mock.calls[0][0];
    const secondUpdater = setBatchVcfCutoffs.mock.calls[1][0];
    expect(firstUpdater({ min_af: 0.05, min_depth: 15 })).toEqual({ min_af: 0.2, min_depth: 15 });
    expect(secondUpdater({ min_af: 0.05, min_depth: 15 })).toEqual({ min_af: 0.05, min_depth: 30 });
  });

  it('gates Submit batch across FASTA/JSON modes, isBatchUploading, empty files, and handles post-submit terminal/download states', () => {
    const submitBatch = vi.fn();
    const { rerender } = render(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchMode: 'fasta',
          batchFastaFiles: [{ rowId: 'r1', uploadId: 'u1', name: 's1.fasta', size: 1024 }],
          batchReferenceFasta: null,
          isBatchUploading: true,
          submitBatch,
        })}
      />,
    );
    expect(screen.getByRole('button', { name: 'Submit batch' })).toBeDisabled();
    expect(screen.getByText('using HSV-1 DB')).toBeInTheDocument();

    rerender(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchMode: 'fasta',
          batchFastaFiles: [],
          isBatchUploading: false,
          submitBatch,
        })}
      />,
    );
    expect(screen.getByRole('button', { name: 'Submit batch' })).toBeDisabled();

    rerender(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchMode: 'fasta',
          batchFastaFiles: [{ rowId: 'r1', uploadId: 'u1', name: 's1.fasta', size: 1024 }],
          batchReferenceFasta: null,
          isBatchUploading: false,
          submitBatch,
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Submit batch' }));
    expect(submitBatch).toHaveBeenCalledTimes(1);

    rerender(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchMode: 'json',
          batchJsonFiles: [{ rowId: 'j1', uploadId: 'u2', name: 's1.json', size: 1024 }],
          batchReferenceFasta: null,
          isBatchUploading: false,
          submitBatch,
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Submit batch' }));
    expect(submitBatch).toHaveBeenCalledTimes(2);

    // Post-submit: all-terminal samples must NOT show "Batch status polling stopped",
    // succeeded row renders ArtifactTableCells links, and isBatchDownloadBusy shows Preparing...
    rerender(
      <BatchAnalyzePanel
        logic={makePanelLogic({
          batchSubmitted: true,
          isBatchPolling: false,
          batchSubmitting: false,
          isBatchDownloadBusy: true,
          batchSamples: [
            {
              job_id: 'j1',
              sample_name: 's1',
              status: 'succeeded',
              errorMessage: null,
              reportHtmlPath: 'h1',
              reportPdfPath: 'p1',
              reportJsonPath: 'j1',
              reportTsvPath: 't1',
              artifactIds: ['h1'],
            },
            { job_id: 'j2', sample_name: 's2', status: 'failed', errorMessage: 'Bad input' },
          ],
        })}
      />,
    );
    expect(screen.queryByText(/Batch status polling stopped/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /preparing/i })).toBeDisabled();
    expect(screen.getByRole('link', { name: 'View' })).toHaveAttribute('href', '/api/report?artifact_id=h1');
    expect(screen.getAllByRole('link', { name: 'Download' }).map((a) => a.getAttribute('href'))).toEqual([
      '/api/artifact?artifact_id=p1',
      '/api/artifact?artifact_id=j1',
      '/api/artifact?artifact_id=t1',
    ]);
  });
});
