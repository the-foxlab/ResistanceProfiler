import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';

import { AnalyzeHeader } from './AnalyzeHeader';

const DEFAULT_PROFILE_MODES = [
  { id: 'vcf', label: 'VCF' },
  { id: 'fasta', label: 'FASTA' },
  { id: 'regenerate', label: 'JSON' },
];

function makeProps(overrides = {}) {
  return {
    uploadProgress: { percent: 0, fileName: '' },
    isUploading: false,
    isBatchUploading: false,
    cancelUpload: vi.fn(),
    cancelBatchUpload: vi.fn(),
    analyzeSubMode: 'single',
    setAnalyzeSubMode: vi.fn(),
    activeProfileMode: 'vcf',
    setActiveProfileMode: vi.fn(),
    batchMode: 'vcf',
    setBatchMode: vi.fn(),
    batchSubmitted: false,
    batchSubmitting: false,
    isProfileBusy: false,
    isRegenerateBusy: false,
    isAnalyzeScopeLocked: false,
    profileModes: DEFAULT_PROFILE_MODES,
    ...overrides,
  };
}

describe('AnalyzeHeader accessibility (WA-053 / WA-054)', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('exposes upload progress as a clamped ARIA progressbar with Cancel button outside the progressbar', () => {
    const cancelUpload = vi.fn();
    const cancelBatchUpload = vi.fn();
    const { rerender } = render(
      <AnalyzeHeader
        {...makeProps({
          uploadProgress: { percent: 42.4, fileName: 'VCF - sample.vcf' },
          isUploading: true,
          cancelUpload,
          cancelBatchUpload,
        })}
      />,
    );

    const progressbar = screen.getByRole('progressbar', { name: 'Upload progress' });
    expect(screen.getByLabelText('Upload progress')).toBe(progressbar);
    expect(progressbar).toHaveAttribute('aria-valuemin', '0');
    expect(progressbar).toHaveAttribute('aria-valuemax', '100');
    expect(progressbar).toHaveAttribute('aria-valuenow', '42');
    expect(progressbar).toHaveAttribute('aria-valuetext', '42% (VCF - sample.vcf)');
    expect(progressbar.querySelector('.upload-progress-fill')).toHaveStyle({ width: '42%' });

    const cancelBtn = screen.getByRole('button', { name: 'Cancel' });
    expect(progressbar).not.toContainElement(cancelBtn);
    fireEvent.click(cancelBtn);
    expect(cancelUpload).toHaveBeenCalledTimes(1);
    expect(cancelBatchUpload).not.toHaveBeenCalled();

    // Clamps negative, over-100, and NaN percentages and formats valuetext without fileName
    rerender(<AnalyzeHeader {...makeProps({ uploadProgress: { percent: -5, fileName: '' } })} />);
    expect(screen.getByRole('progressbar', { name: 'Upload progress' })).toHaveAttribute('aria-valuenow', '0');
    expect(screen.getByRole('progressbar', { name: 'Upload progress' })).toHaveAttribute('aria-valuetext', '0%');

    rerender(
      <AnalyzeHeader
        {...makeProps({
          uploadProgress: { percent: 150, fileName: 'FASTA - big.fa' },
          isBatchUploading: true,
          cancelUpload,
          cancelBatchUpload,
        })}
      />,
    );
    expect(screen.getByRole('progressbar', { name: 'Upload progress' })).toHaveAttribute('aria-valuenow', '100');
    expect(screen.getByRole('progressbar', { name: 'Upload progress' })).toHaveAttribute(
      'aria-valuetext',
      '100% (FASTA - big.fa)',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(cancelBatchUpload).toHaveBeenCalledTimes(1);

    rerender(<AnalyzeHeader {...makeProps({ uploadProgress: { percent: Number.NaN, fileName: '' } })} />);
    expect(screen.getByRole('progressbar', { name: 'Upload progress' })).toHaveAttribute('aria-valuenow', '0');
  });

  it('uses role="group" (not role="switch") and aria-pressed on the submode switcher', () => {
    const setAnalyzeSubMode = vi.fn();
    const setActiveProfileMode = vi.fn();
    const setBatchMode = vi.fn();
    const { rerender } = render(
      <AnalyzeHeader
        {...makeProps({
          analyzeSubMode: 'single',
          setAnalyzeSubMode,
          setActiveProfileMode,
          setBatchMode,
        })}
      />,
    );

    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
    const group = screen.getByRole('group', { name: 'One or multiple samples' });
    expect(group).toHaveAttribute('data-state', 'single');

    const oneBtn = within(group).getByRole('button', { name: /one sample/i });
    const multiBtn = within(group).getByRole('button', { name: /multiple samples/i });
    expect(oneBtn).toHaveAttribute('aria-pressed', 'true');
    expect(multiBtn).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(multiBtn);
    expect(setBatchMode).toHaveBeenCalledWith('vcf');
    expect(setAnalyzeSubMode).toHaveBeenCalledWith('batch');

    rerender(
      <AnalyzeHeader
        {...makeProps({
          analyzeSubMode: 'batch',
          setAnalyzeSubMode,
          setActiveProfileMode,
          setBatchMode,
          isAnalyzeScopeLocked: true,
        })}
      />,
    );
    expect(screen.getByRole('group', { name: 'One or multiple samples' })).toHaveAttribute('data-state', 'batch');
    expect(screen.getByRole('button', { name: /one sample/i })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: /one sample/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /multiple samples/i })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /multiple samples/i })).toBeDisabled();
  });

  it('sets aria-pressed on Profiling mode and Batch mode toggle buttons and respects busy/submitted state', () => {
    const setActiveProfileMode = vi.fn();
    const setBatchMode = vi.fn();
    const { rerender } = render(
      <AnalyzeHeader
        {...makeProps({
          analyzeSubMode: 'single',
          activeProfileMode: 'fasta',
          setActiveProfileMode,
          isProfileBusy: true,
        })}
      />,
    );

    const profilingGroup = screen.getByRole('group', { name: 'Profiling mode' });
    expect(within(profilingGroup).getByRole('button', { name: 'VCF' })).toHaveAttribute('aria-pressed', 'false');
    expect(within(profilingGroup).getByRole('button', { name: 'FASTA' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(profilingGroup).getByRole('button', { name: 'JSON' })).toHaveAttribute('aria-pressed', 'false');
    expect(within(profilingGroup).getByRole('button', { name: 'VCF' })).toBeDisabled();

    rerender(
      <AnalyzeHeader
        {...makeProps({
          analyzeSubMode: 'single',
          activeProfileMode: 'vcf',
          setActiveProfileMode,
          isProfileBusy: false,
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'JSON' }));
    expect(setActiveProfileMode).toHaveBeenCalledWith('regenerate');

    // Batch mode buttons
    rerender(
      <AnalyzeHeader
        {...makeProps({
          analyzeSubMode: 'batch',
          batchMode: 'json',
          setBatchMode,
          batchSubmitted: false,
          batchSubmitting: false,
        })}
      />,
    );
    const batchGroup = screen.getByRole('group', { name: 'Batch mode' });
    expect(within(batchGroup).getByRole('button', { name: 'VCF' })).toHaveAttribute('aria-pressed', 'false');
    expect(within(batchGroup).getByRole('button', { name: 'FASTA' })).toHaveAttribute('aria-pressed', 'false');
    expect(within(batchGroup).getByRole('button', { name: 'JSON' })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(within(batchGroup).getByRole('button', { name: 'FASTA' }));
    expect(setBatchMode).toHaveBeenCalledWith('fasta');

    // Hidden when batchSubmitted is true
    rerender(
      <AnalyzeHeader
        {...makeProps({
          analyzeSubMode: 'batch',
          batchSubmitted: true,
        })}
      />,
    );
    expect(screen.queryByRole('group', { name: 'Batch mode' })).not.toBeInTheDocument();
  });

  it('handles One Sample click in batch mode, regenerate busy isolation, batchSubmitting disablement, and hidden Cancel when idle', () => {
    const setActiveProfileMode = vi.fn();
    const setAnalyzeSubMode = vi.fn();

    const { rerender } = render(
      <AnalyzeHeader
        {...makeProps({
          analyzeSubMode: 'batch',
          batchMode: 'vcf',
          batchSubmitted: false,
          batchSubmitting: true,
          isUploading: false,
          isBatchUploading: false,
          setActiveProfileMode,
          setAnalyzeSubMode,
        })}
      />,
    );

    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument();

    const batchGroup = screen.getByRole('group', { name: 'Batch mode' });
    for (const btn of within(batchGroup).getAllByRole('button')) {
      expect(btn).toBeDisabled();
    }

    fireEvent.click(screen.getByRole('button', { name: /one sample/i }));
    expect(setActiveProfileMode).toHaveBeenCalledWith('vcf');
    expect(setAnalyzeSubMode).toHaveBeenCalledWith('single');

    // In regenerate mode, isProfileBusy does NOT disable mode buttons, while isRegenerateBusy DOES.
    rerender(
      <AnalyzeHeader
        {...makeProps({
          analyzeSubMode: 'single',
          activeProfileMode: 'regenerate',
          isProfileBusy: true,
          isRegenerateBusy: false,
        })}
      />,
    );
    const profGroup = screen.getByRole('group', { name: 'Profiling mode' });
    for (const btn of within(profGroup).getAllByRole('button')) {
      expect(btn).toBeEnabled();
    }

    rerender(
      <AnalyzeHeader
        {...makeProps({
          analyzeSubMode: 'single',
          activeProfileMode: 'regenerate',
          isProfileBusy: false,
          isRegenerateBusy: true,
        })}
      />,
    );
    for (const btn of within(screen.getByRole('group', { name: 'Profiling mode' })).getAllByRole('button')) {
      expect(btn).toBeDisabled();
    }
  });
});
