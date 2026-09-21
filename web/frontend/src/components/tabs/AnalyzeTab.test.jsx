import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, cleanup, fireEvent, screen, act } from '@testing-library/react';

import { AnalyzeTab } from './AnalyzeTab';

// AnalyzeTab destructures a large prop surface, but only a handful are read
// on the render path exercised here (the report iframe + plot modal). The
// rest are passed through to event handlers and never invoked during these
// tests, so no-op stubs suffice.
function minimalProps(overrides = {}) {
  return {
    selectedDatabase: null,
    vcfInput: '',
    setVcfInput: () => {},
    fastaInput: '',
    setFastaInput: () => {},
    jsonInputId: '',
    isRegenerateBusy: false,
    runRegenerateFromJson: () => {},
    uploadFastaFile: () => {},
    uploadVcfFile: () => {},
    uploadReferenceFile: () => {},
    uploadBamFile: () => {},
    uploadJsonFile: () => {},
    uploadProgress: { percent: 0, name: '' },
    isUploading: false,
    isBatchUploading: false,
    cancelUpload: () => {},
    cancelBatchUpload: () => {},
    activeProfileMode: '',
    setActiveProfileMode: () => {},
    analyzeSubMode: 'single',
    setAnalyzeSubMode: () => {},
    isProfileBusy: false,
    canCancelJob: false,
    isCancelingJob: false,
    cancelActiveJob: () => {},
    runSelectedProfile: () => {},
    runExampleProfile: () => {},
    statusError: '',
    selectedProfileReportPath: '',
    setSelectedProfileReportPath: () => {},
    reportOptions: [],
    buildReportUrl: (path) => `http://127.0.0.1:8000/api/report?artifact_id=${path}`,
    buildArtifactUrl: (path) => `http://127.0.0.1:8000/api/artifact?artifact_id=${path}`,
    batchMode: false,
    setBatchMode: () => {},
    batchVcfFiles: [],
    batchFastaFiles: [],
    batchReferenceFasta: null,
    batchSamples: [],
    batchSubmitting: false,
    isBatchDownloadBusy: false,
    batchError: '',
    batchRateLimitCooldown: 0,
    setBatchRateLimitCooldown: () => {},
    batchSubmitted: false,
    batchMaxSamples: 25,
    sampleLimitPerMinute: 25,
    batchVcfCutoffs: {},
    setBatchVcfCutoffs: () => {},
    addBatchVcfFiles: () => {},
    addBatchFastaFiles: () => {},
    addBatchBamFiles: () => {},
    attachBatchBam: () => {},
    removeBatchFile: () => {},
    uploadBatchReferenceFasta: () => {},
    submitBatch: () => {},
    downloadAllBatchArtifacts: () => {},
    resetBatch: () => {},
    inlineReportPath: '',
    isAnalyzeScopeLocked: false,
    PROFILE_MODES: [],
    ...overrides,
  };
}

// Dispatch a MessageEvent on window as if it came from the report iframe.
// Wrapped in act() so React flushes the resulting state update synchronously
// before the assertion runs.
function dispatchReportMessage(type, payload, { origin, source } = {}) {
  const event = new MessageEvent('message', {
    data: { type, ...payload },
    origin: origin ?? 'http://127.0.0.1:8000',
    source: source ?? null,
  });
  act(() => {
    window.dispatchEvent(event);
  });
}

describe('AnalyzeTab embedded report messaging', () => {
  afterEach(() => {
    cleanup();
  });

  it('ignores respro:open-plot messages from an unexpected origin', () => {
    render(<AnalyzeTab {...minimalProps({ inlineReportPath: 'r1' })} />);
    // Report origin is http://127.0.0.1:8000 (from buildReportUrl); post
    // from a foreign origin.
    dispatchReportMessage('respro:open-plot', { src: 'blob:evil', alt: 'x' }, {
      origin: 'https://evil.example',
    });
    expect(screen.queryByRole('dialog', { name: /resistance plot/i })).not.toBeInTheDocument();
  });

  it('opens the hosted plot modal from a same-origin respro:open-plot payload', () => {
    render(<AnalyzeTab {...minimalProps({ inlineReportPath: 'r1' })} />);
    const frame = document.querySelector('.workspace-frame');
    dispatchReportMessage('respro:open-plot', { src: 'blob:plot', alt: 'Resistance plot' }, {
      source: frame?.contentWindow ?? null,
    });
    const dialog = screen.getByRole('dialog', { name: /resistance plot/i });
    expect(dialog).toBeInTheDocument();
    const img = dialog.querySelector('.report-preview-plot-image');
    expect(img).toHaveAttribute('src', 'blob:plot');
    expect(img).toHaveAttribute('alt', 'Resistance plot');
  });

  it('closes the hosted plot modal on Escape', () => {
    render(<AnalyzeTab {...minimalProps({ inlineReportPath: 'r1' })} />);
    const frame = document.querySelector('.workspace-frame');
    dispatchReportMessage('respro:open-plot', { src: 'blob:plot' }, {
      source: frame?.contentWindow ?? null,
    });
    expect(screen.getByRole('dialog', { name: /resistance plot/i })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: /resistance plot/i })).not.toBeInTheDocument();
  });

  it('applies a respro:report-height payload as the iframe height', () => {
    render(<AnalyzeTab {...minimalProps({ inlineReportPath: 'r1' })} />);
    const frame = document.querySelector('.workspace-frame');
    dispatchReportMessage('respro:report-height', { height: 1234 }, {
      source: frame?.contentWindow ?? null,
    });
    expect(frame.style.height).toBe('1236px');
  });

  it('ignores respro:report-height from a foreign origin', () => {
    render(<AnalyzeTab {...minimalProps({ inlineReportPath: 'r1' })} />);
    const frame = document.querySelector('.workspace-frame');
    dispatchReportMessage('respro:report-height', { height: 9999 }, {
      origin: 'https://evil.example',
      source: frame?.contentWindow ?? null,
    });
    expect(frame.style.height).toBe('');
  });

  it('ignores respro:open-structure messages from an unexpected origin', () => {
    render(<AnalyzeTab {...minimalProps({ inlineReportPath: 'r1' })} />);
    dispatchReportMessage('respro:open-structure', { src: 'blob:evil', title: 'Drug X' }, {
      origin: 'https://evil.example',
    });
    expect(screen.queryByRole('dialog', { name: /chemical structure of drug x/i })).not.toBeInTheDocument();
  });

  it('opens the hosted structure modal from a same-origin respro:open-structure payload', () => {
    render(<AnalyzeTab {...minimalProps({ inlineReportPath: 'r1' })} />);
    const frame = document.querySelector('.workspace-frame');
    dispatchReportMessage('respro:open-structure', { src: 'blob:struct', title: 'Zidovudine' }, {
      source: frame?.contentWindow ?? null,
    });
    const dialog = screen.getByRole('dialog', { name: /chemical structure of zidovudine/i });
    expect(dialog).toBeInTheDocument();
    const img = dialog.querySelector('.report-preview-plot-image');
    expect(img).toHaveAttribute('src', 'blob:struct');
    expect(img).toHaveAttribute('alt', 'Chemical structure of Zidovudine');
  });

  it('closes the hosted structure modal on Escape', () => {
    render(<AnalyzeTab {...minimalProps({ inlineReportPath: 'r1' })} />);
    const frame = document.querySelector('.workspace-frame');
    dispatchReportMessage('respro:open-structure', { src: 'blob:struct', title: 'Drug X' }, {
      source: frame?.contentWindow ?? null,
    });
    expect(screen.getByRole('dialog', { name: /chemical structure of drug x/i })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: /chemical structure of drug x/i })).not.toBeInTheDocument();
  });

  it('ignores respro:open-sequence messages from an unexpected origin', () => {
    render(<AnalyzeTab {...minimalProps({ inlineReportPath: 'r1' })} />);
    dispatchReportMessage('respro:open-sequence', { title: 'PR', ntSequence: 'ACGT', aaSequence: 'M' }, {
      origin: 'https://evil.example',
    });
    expect(screen.queryByRole('dialog', { name: /feature sequence/i })).not.toBeInTheDocument();
  });

  it('opens the hosted sequence modal from a same-origin respro:open-sequence payload', () => {
    render(<AnalyzeTab {...minimalProps({ inlineReportPath: 'r1' })} />);
    const frame = document.querySelector('.workspace-frame');
    dispatchReportMessage('respro:open-sequence', {
      title: 'Protease',
      ntSequence: 'ACGTACGT',
      aaSequence: 'TVTV',
    }, { source: frame?.contentWindow ?? null });
    const dialog = screen.getByRole('dialog', { name: /feature sequence/i });
    expect(dialog).toBeInTheDocument();
    expect(dialog.querySelector('.report-preview-sequence-title')).toHaveTextContent('Protease');
    // Defaults to DNA view when ntSequence is present.
    const block = dialog.querySelector('.report-preview-sequence-block');
    expect(block).toHaveTextContent('ACGTACGT');
  });

  it('switches the hosted sequence modal between DNA and Protein views', () => {
    render(<AnalyzeTab {...minimalProps({ inlineReportPath: 'r1' })} />);
    const frame = document.querySelector('.workspace-frame');
    dispatchReportMessage('respro:open-sequence', {
      title: 'Protease',
      ntSequence: 'ACGTACGT',
      aaSequence: 'TVTV',
    }, { source: frame?.contentWindow ?? null });
    const dialog = screen.getByRole('dialog', { name: /feature sequence/i });
    const block = dialog.querySelector('.report-preview-sequence-block');
    expect(block).toHaveTextContent('ACGTACGT');
    fireEvent.click(dialog.querySelector('.seq-toggle-btn:last-child'));
    expect(block).toHaveTextContent('TVTV');
    fireEvent.click(dialog.querySelector('.seq-toggle-btn:first-child'));
    expect(block).toHaveTextContent('ACGTACGT');
  });

  it('closes the hosted sequence modal on Escape', () => {
    render(<AnalyzeTab {...minimalProps({ inlineReportPath: 'r1' })} />);
    const frame = document.querySelector('.workspace-frame');
    dispatchReportMessage('respro:open-sequence', { title: 'PR', ntSequence: 'ACGT', aaSequence: 'M' }, {
      source: frame?.contentWindow ?? null,
    });
    expect(screen.getByRole('dialog', { name: /feature sequence/i })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: /feature sequence/i })).not.toBeInTheDocument();
  });
});

describe('AnalyzeTab reportOrigin with relative report URLs', () => {
  afterEach(() => {
    cleanup();
  });

  // Production topology: the webapp serves both the frontend shell and the
  // report from the same origin, so buildReportUrl returns a RELATIVE URL.
  // Regression: bare new URL(src) throws on relative URLs, silently yielding
  // reportOrigin='' so every respro:* message from the iframe was rejected —
  // the plot/structure/sequence popups never opened.
  const relativeBuildReportUrl = (path) => `/api/report?artifact_id=${path}`;

  function dispatchFromFrame(type, payload) {
    const frame = document.querySelector('.workspace-frame');
    act(() => {
      window.dispatchEvent(new MessageEvent('message', {
        data: { type, ...payload },
        // jsdom resolves the relative iframe src against its own location,
        // so the browser-reported event origin equals that resolved origin.
        origin: new URL(frame?.src, window.location.href).origin,
        source: frame?.contentWindow ?? null,
      }));
    });
  }

  it('accepts respro:open-plot when the report URL is relative (same-origin deployment)', () => {
    render(<AnalyzeTab {...minimalProps({
      inlineReportPath: 'r1',
      buildReportUrl: relativeBuildReportUrl,
    })} />);
    dispatchFromFrame('respro:open-plot', { src: 'blob:plot', alt: 'Resistance plot' });
    const dialog = screen.getByRole('dialog', { name: /resistance plot/i });
    expect(dialog.querySelector('.report-preview-plot-image')).toHaveAttribute('src', 'blob:plot');
  });

  it('keeps accepting respro:open-plot after a submode switch remounts the iframe', () => {
    // Reproduces the reported bug: single → batch → single unmounts and
    // remounts the iframe. On the remount render reportFrameRef.current is
    // still null, so reportOrigin is derived from the RELATIVE fallback URL;
    // with the old bare new URL(src) that threw and left reportOrigin=''
    // permanently (no later re-render recomputes it), so the popup never
    // opened until the sample was rerun.
    const props = minimalProps({
      inlineReportPath: 'r1',
      buildReportUrl: relativeBuildReportUrl,
      batchMode: 'vcf',
    });
    const { rerender } = render(<AnalyzeTab {...props} analyzeSubMode="single" />);
    // Switch to batch: the report tile (and its iframe) unmounts.
    rerender(<AnalyzeTab {...props} analyzeSubMode="batch" />);
    // Switch back to single: the iframe remounts; refs are null during the
    // render that derives reportOrigin.
    rerender(<AnalyzeTab {...props} analyzeSubMode="single" />);
    dispatchFromFrame('respro:open-plot', { src: 'blob:plot', alt: 'Resistance plot' });
    expect(screen.getByRole('dialog', { name: /resistance plot/i })).toBeInTheDocument();
  });

  it('accepts respro:open-sequence and respro:open-structure with a relative report URL', () => {
    render(<AnalyzeTab {...minimalProps({
      inlineReportPath: 'r1',
      buildReportUrl: relativeBuildReportUrl,
    })} />);
    dispatchFromFrame('respro:open-structure', { src: 'blob:struct', title: 'Zidovudine' });
    expect(screen.getByRole('dialog', { name: /chemical structure of zidovudine/i })).toBeInTheDocument();
    dispatchFromFrame('respro:open-sequence', { title: 'PR', ntSequence: 'ACGT', aaSequence: 'M' });
    expect(screen.getByRole('dialog', { name: /feature sequence/i })).toBeInTheDocument();
  });
});

describe('AnalyzeTab submit gating during upload', () => {
  afterEach(() => {
    cleanup();
  });

  it('disables the single Analyze button while an upload is in flight', () => {
    render(<AnalyzeTab {...minimalProps({
      analyzeSubMode: 'single',
      activeProfileMode: 'fasta',
      fastaInput: { fasta_id: 'up-1', input_display_name: 'a.fasta' },
      isProfileBusy: false,
      isUploading: true,
    })} />);

    const button = screen.getByRole('button', { name: 'Analyze' });
    expect(button).toBeDisabled();
  });

  it('re-enables the single Analyze button once the upload completes', () => {
    render(<AnalyzeTab {...minimalProps({
      analyzeSubMode: 'single',
      activeProfileMode: 'fasta',
      fastaInput: { fasta_id: 'up-1', input_display_name: 'a.fasta' },
      isProfileBusy: false,
      isUploading: false,
    })} />);

    const button = screen.getByRole('button', { name: 'Analyze' });
    expect(button).not.toBeDisabled();
  });

  it('disables the batch Submit button while an upload is in flight', () => {
    render(<AnalyzeTab {...minimalProps({
      analyzeSubMode: 'batch',
      batchSubmitted: false,
      batchMode: 'fasta',
      batchFastaFiles: [{ uploadId: 'up-1', name: 'a.fasta', size: 1 }],
      batchSubmitting: false,
      batchRateLimitCooldown: 0,
      isBatchUploading: true,
    })} />);

    const button = screen.getByRole('button', { name: 'Submit batch' });
    expect(button).toBeDisabled();
  });

  it('re-enables the batch Submit button once the upload completes', () => {
    render(<AnalyzeTab {...minimalProps({
      analyzeSubMode: 'batch',
      batchSubmitted: false,
      batchMode: 'fasta',
      batchFastaFiles: [{ uploadId: 'up-1', name: 'a.fasta', size: 1 }],
      batchSubmitting: false,
      batchRateLimitCooldown: 0,
      isBatchUploading: false,
    })} />);

    const button = screen.getByRole('button', { name: 'Submit batch' });
    expect(button).not.toBeDisabled();
  });
});

describe('AnalyzeTab cancel-upload button', () => {
  afterEach(() => {
    cleanup();
  });

  it('shows a cancel button during a single upload that calls cancelUpload', () => {
    const cancelUpload = vi.fn();
    render(<AnalyzeTab {...minimalProps({
      analyzeSubMode: 'single',
      isUploading: true,
      cancelUpload,
    })} />);

    const cancel = screen.getByRole('button', { name: 'Cancel' });
    expect(cancel).toBeInTheDocument();
    expect(cancel).toHaveAttribute('title', 'Cancel');
    fireEvent.click(cancel);
    expect(cancelUpload).toHaveBeenCalled();
  });

  it('hides the cancel button when no single upload is in flight', () => {
    render(<AnalyzeTab {...minimalProps({
      analyzeSubMode: 'single',
      isUploading: false,
    })} />);

    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();
  });

  it('shows a cancel button during a batch upload that calls cancelBatchUpload', () => {
    const cancelBatchUpload = vi.fn();
    render(<AnalyzeTab {...minimalProps({
      analyzeSubMode: 'batch',
      batchSubmitted: false,
      batchMode: 'fasta',
      batchFastaFiles: [{ uploadId: 'up-1', name: 'a.fasta', size: 1 }],
      isBatchUploading: true,
      cancelBatchUpload,
    })} />);

    const cancel = screen.getByRole('button', { name: 'Cancel' });
    expect(cancel).toBeInTheDocument();
    fireEvent.click(cancel);
    expect(cancelBatchUpload).toHaveBeenCalled();
  });

  it('does not call resetBatch when switching to batch mode (preserves uploads)', () => {
    const resetBatch = vi.fn();
    const setAnalyzeSubMode = vi.fn();
    render(<AnalyzeTab {...minimalProps({
      analyzeSubMode: 'single',
      setAnalyzeSubMode,
      resetBatch,
    })} />);

    const batchBtn = screen.getByRole('button', { name: /multiple samples/i });
    fireEvent.click(batchBtn);
    // Switching modes must preserve the upload overview — resetBatch is not called.
    expect(resetBatch).not.toHaveBeenCalled();
    expect(setAnalyzeSubMode).toHaveBeenCalledWith('batch');
  });
});

describe('AnalyzeTab clear-all button (batch upload overview)', () => {
  afterEach(() => {
    cleanup();
  });

  it('shows a Clear all button in the pre-submission batch form when files are uploaded', () => {
    render(<AnalyzeTab {...minimalProps({
      analyzeSubMode: 'batch',
      batchSubmitted: false,
      batchMode: 'fasta',
      batchFastaFiles: [{ uploadId: 'up-1', name: 'a.fasta', size: 1 }],
    })} />);

    expect(screen.getByRole('button', { name: /clear all/i })).toBeInTheDocument();
  });

  it('calls resetBatch when Clear all is clicked', () => {
    const resetBatch = vi.fn();
    render(<AnalyzeTab {...minimalProps({
      analyzeSubMode: 'batch',
      batchSubmitted: false,
      batchMode: 'fasta',
      batchFastaFiles: [{ uploadId: 'up-1', name: 'a.fasta', size: 1 }],
      resetBatch,
    })} />);

    fireEvent.click(screen.getByRole('button', { name: /clear all/i }));
    expect(resetBatch).toHaveBeenCalledTimes(1);
  });

  it('hides the Clear all button when no files are uploaded', () => {
    render(<AnalyzeTab {...minimalProps({
      analyzeSubMode: 'batch',
      batchSubmitted: false,
      batchMode: 'fasta',
      batchFastaFiles: [],
    })} />);

    expect(screen.queryByRole('button', { name: /clear all/i })).not.toBeInTheDocument();
  });

  it('hides the Clear all button in single-sample mode', () => {
    render(<AnalyzeTab {...minimalProps({
      analyzeSubMode: 'single',
    })} />);

    expect(screen.queryByRole('button', { name: /clear all/i })).not.toBeInTheDocument();
  });
});

describe('AnalyzeTab upload progress visibility', () => {
  afterEach(() => {
    cleanup();
  });

  it('shows the upload progress bar in the batch results view', () => {
    render(<AnalyzeTab {...minimalProps({
      analyzeSubMode: 'batch',
      batchSubmitted: true,
      batchSamples: [{ job_id: 'j1', sample_name: 's1', status: 'succeeded' }],
      uploadProgress: { percent: 100, fileName: 'a.fasta' },
    })} />);

    expect(screen.getByLabelText('Upload progress')).toBeInTheDocument();
  });

  it('shows the upload progress bar in the single-sample results view', () => {
    render(<AnalyzeTab {...minimalProps({
      analyzeSubMode: 'single',
      inlineReportPath: 'reports/r1.html',
      uploadProgress: { percent: 100, fileName: 'a.fasta' },
    })} />);

    expect(screen.getByLabelText('Upload progress')).toBeInTheDocument();
  });

  it('shows the upload progress bar in the single-sample input view', () => {
    render(<AnalyzeTab {...minimalProps({
      analyzeSubMode: 'single',
      uploadProgress: { percent: 0, fileName: '' },
    })} />);

    expect(screen.getByLabelText('Upload progress')).toBeInTheDocument();
  });
});
