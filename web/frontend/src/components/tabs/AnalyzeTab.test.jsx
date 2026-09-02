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
});
