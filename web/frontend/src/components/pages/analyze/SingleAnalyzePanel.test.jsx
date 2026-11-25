import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { SingleAnalyzePanel } from './SingleAnalyzePanel';

function makePanelLogic(overrides = {}) {
  return {
    selectedDatabase: { id: 'db1', display_name: 'HSVdb' },
    vcfInput: {
      vcf_id: '',
      input_display_name: '',
      reference_id: '',
      bam_id: null,
      sample: '',
      min_af: 0.01,
      min_depth: 10,
    },
    setVcfInput: vi.fn(),
    fastaInput: {
      fasta_id: '',
      input_display_name: '',
      sample: '',
    },
    setFastaInput: vi.fn(),
    jsonInputId: '',
    isRegenerateBusy: false,
    runRegenerateFromJson: vi.fn(),
    uploadFastaFile: vi.fn(),
    uploadVcfFile: vi.fn(),
    uploadReferenceFile: vi.fn(),
    uploadBamFile: vi.fn(),
    uploadJsonFile: vi.fn(),
    isUploading: false,
    activeProfileMode: 'vcf',
    isProfileBusy: false,
    canCancelJob: false,
    isCancelingJob: false,
    cancelActiveJob: vi.fn(),
    runSelectedProfile: vi.fn(),
    statusError: '',
    selectedProfileReportPath: '',
    setSelectedProfileReportPath: vi.fn(),
    reportOptions: [],
    buildReportUrl: (p) => `/api/report?artifact_id=${p}`,
    buildArtifactUrl: (p) => `/api/artifact?artifact_id=${p}`,
    inlineReportPath: '',
    ...overrides,
  };
}

function StatefulPanelHarness({ mode = 'vcf', onUploadReady }) {
  const [vcfInput, setVcfInput] = useState({
    vcf_id: '',
    input_display_name: '',
    reference_id: 'up-ref',
    bam_id: null,
    sample: '',
    min_af: 0.01,
    min_depth: 10,
  });
  const [fastaInput, setFastaInput] = useState({
    fasta_id: '',
    input_display_name: '',
    sample: '',
  });

  onUploadReady?.({
    completeVcfUpload: (uploadId) =>
      setVcfInput((prev) => ({ ...prev, vcf_id: uploadId, input_display_name: 'sample.vcf' })),
    completeFastaUpload: (uploadId) =>
      setFastaInput((prev) => ({ ...prev, fasta_id: uploadId, input_display_name: 'sample.fasta' })),
  });

  const logic = makePanelLogic({
    activeProfileMode: mode,
    vcfInput,
    setVcfInput,
    fastaInput,
    setFastaInput,
  });

  return (
    <>
      <SingleAnalyzePanel logic={logic} />
      <output data-testid="vcf-state">{JSON.stringify(vcfInput)}</output>
      <output data-testid="fasta-state">{JSON.stringify(fastaInput)}</output>
    </>
  );
}

describe('SingleAnalyzePanel', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('uses functional state updaters so concurrent VCF upload completion does not clobber sample or cutoffs', () => {
    let callbacks;
    render(
      <StatefulPanelHarness
        mode="vcf"
        onUploadReady={(cb) => {
          callbacks = cb;
        }}
      />,
    );

    const sampleInput = document.querySelector('[data-tour-target="vcf-sample-name"] input');
    const minAfInput = document.querySelector('[data-tour-target="vcf-frequency-cutoff"] input');
    const minDepthInput = document.querySelector('[data-tour-target="vcf-coverage-cutoff"] input');

    // Simulate upload completing synchronously in the same act batch as user edits.
    act(() => {
      callbacks.completeVcfUpload('up-vcf-1');
      fireEvent.change(sampleInput, { target: { value: 'Patient_01' } });
      fireEvent.change(minAfInput, { target: { value: '0.15' } });
      fireEvent.change(minDepthInput, { target: { value: '25' } });
    });

    const vcfState = JSON.parse(screen.getByTestId('vcf-state').textContent);
    expect(vcfState.vcf_id).toBe('up-vcf-1');
    expect(vcfState.input_display_name).toBe('sample.vcf');
    expect(vcfState.sample).toBe('Patient_01');
    expect(vcfState.min_af).toBe(0.15);
    expect(vcfState.min_depth).toBe(25);
  });

  it('uses functional state updaters so concurrent FASTA upload completion does not clobber sample name', () => {
    let callbacks;
    render(
      <StatefulPanelHarness
        mode="fasta"
        onUploadReady={(cb) => {
          callbacks = cb;
        }}
      />,
    );

    const sampleInput = document.querySelector('.profile-upload-row-fasta .sample-name-input');
    act(() => {
      callbacks.completeFastaUpload('up-fasta-1');
      fireEvent.change(sampleInput, { target: { value: 'FastaSample' } });
    });

    const fastaState = JSON.parse(screen.getByTestId('fasta-state').textContent);
    expect(fastaState.fasta_id).toBe('up-fasta-1');
    expect(fastaState.input_display_name).toBe('sample.fasta');
    expect(fastaState.sample).toBe('FastaSample');
  });

  it('disables Results JSON file input and Regenerate button when isRegenerateBusy or isProfileBusy is true', () => {
    const { rerender } = render(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'regenerate',
          jsonInputId: 'json-1',
          isRegenerateBusy: false,
          isProfileBusy: false,
        })}
      />,
    );

    const fileInput = document.querySelector('.profile-upload-row-regenerate input[type="file"]');
    expect(fileInput).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Regenerate' })).toBeEnabled();

    rerender(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'regenerate',
          jsonInputId: 'json-1',
          isRegenerateBusy: true,
          isProfileBusy: false,
        })}
      />,
    );
    expect(document.querySelector('.profile-upload-row-regenerate input[type="file"]')).toBeDisabled();
    expect(screen.getByRole('button', { name: /regenerate/i })).toBeDisabled();

    rerender(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'regenerate',
          jsonInputId: 'json-1',
          isRegenerateBusy: false,
          isProfileBusy: true,
        })}
      />,
    );
    expect(document.querySelector('.profile-upload-row-regenerate input[type="file"]')).toBeDisabled();
    expect(screen.getByRole('button', { name: /regenerate/i })).toBeDisabled();

    rerender(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'regenerate',
          jsonInputId: '',
          isRegenerateBusy: false,
          isProfileBusy: false,
        })}
      />,
    );
    expect(document.querySelector('.profile-upload-row-regenerate input[type="file"]')).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Regenerate' })).toBeDisabled();
  });

  it('gates VCF/FASTA inputs and submit on busy/required IDs and wires Cancel job button', () => {
    const cancelActiveJob = vi.fn();
    const { rerender } = render(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'vcf',
          vcfInput: {
            vcf_id: 'v1',
            input_display_name: 'a.vcf',
            reference_id: 'r1',
            bam_id: null,
            sample: 's1',
            min_af: 0.01,
            min_depth: 10,
          },
          isProfileBusy: true,
          canCancelJob: true,
          isCancelingJob: false,
          cancelActiveJob,
        })}
      />,
    );

    const vcfInputs = document.querySelectorAll('.profile-upload-row-vcf input');
    expect(vcfInputs.length).toBeGreaterThanOrEqual(4);
    for (const input of vcfInputs) {
      expect(input).toBeDisabled();
    }
    expect(screen.getByRole('button', { name: /analyze/i })).toBeDisabled();

    const cancelBtn = screen.getByRole('button', { name: 'Cancel job' });
    expect(cancelBtn).toBeEnabled();
    fireEvent.click(cancelBtn);
    expect(cancelActiveJob).toHaveBeenCalledTimes(1);

    rerender(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'fasta',
          fastaInput: { fasta_id: 'f1', input_display_name: 'a.fasta', sample: 's1' },
          isRegenerateBusy: true,
          canCancelJob: true,
          isCancelingJob: true,
        })}
      />,
    );
    for (const input of document.querySelectorAll('.profile-upload-row-fasta input')) {
      expect(input).toBeDisabled();
    }
    expect(screen.getByRole('button', { name: /analyze/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Canceling...' })).toBeDisabled();
  });

  it('announces statusError with role="alert" + --color-error and labels the session report selector (WA-053/WA-027)', () => {
    const setSelectedProfileReportPath = vi.fn();
    const { rerender } = render(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          statusError: '',
          reportOptions: [],
        })}
      />,
    );
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: /select session report/i })).not.toBeInTheDocument();

    rerender(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          statusError: 'Upload validation failed',
          selectedProfileReportPath: 'rep-1',
          setSelectedProfileReportPath,
          reportOptions: [
            { path: 'rep-1', label: 'Sample 1 (REF1)', pdfPath: 'pdf-1' },
            { path: 'rep-2', label: 'Sample 2 (REF1)' },
          ],
        })}
      />,
    );

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Upload validation failed');
    expect(alert).toHaveStyle({ color: 'var(--color-error)' });

    const select = screen.getByRole('combobox', { name: 'Select session report' });
    expect(select).toHaveValue('rep-1');
    fireEvent.change(select, { target: { value: 'rep-2' } });
    expect(setSelectedProfileReportPath).toHaveBeenCalledWith('rep-2');
  });

  it('exposes accessible names on inputs and InfoButtons across vcf, fasta, and regenerate modes', () => {
    const { rerender } = render(<SingleAnalyzePanel logic={makePanelLogic({ activeProfileMode: 'vcf' })} />);
    expect(screen.getByLabelText(/VCF file/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Reference FASTA/)).toBeInTheDocument();
    expect(screen.getByLabelText(/BAM file/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Sample name/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'VCF help' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reference FASTA help' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'BAM help' })).toBeInTheDocument();

    rerender(<SingleAnalyzePanel logic={makePanelLogic({ activeProfileMode: 'fasta' })} />);
    expect(screen.getByLabelText(/FASTA file/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Sample name/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'FASTA help' })).toBeInTheDocument();

    rerender(<SingleAnalyzePanel logic={makePanelLogic({ activeProfileMode: 'regenerate' })} />);
    expect(screen.getByLabelText(/Results JSON/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Results JSON help' })).toBeInTheDocument();
  });

  it('wires file input onChange callbacks across modes and ignores empty FileLists', () => {
    const uploadVcfFile = vi.fn();
    const uploadReferenceFile = vi.fn();
    const uploadBamFile = vi.fn();
    const uploadFastaFile = vi.fn();
    const uploadJsonFile = vi.fn();

    const { rerender } = render(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'vcf',
          uploadVcfFile,
          uploadReferenceFile,
          uploadBamFile,
        })}
      />,
    );

    const vcfFile = new File(['##VCF'], 's.vcf');
    const refFile = new File(['>r\nACGT'], 'r.fasta');
    const bamFile = new File(['BAM'], 's.bam');
    const vcfInput = screen.getByLabelText(/VCF file/, { selector: 'input' });
    fireEvent.change(vcfInput, { target: { files: [] } });
    expect(uploadVcfFile).not.toHaveBeenCalled();
    fireEvent.change(vcfInput, { target: { files: [vcfFile] } });
    expect(uploadVcfFile).toHaveBeenCalledWith(vcfFile);

    fireEvent.change(screen.getByLabelText(/Reference FASTA/, { selector: 'input' }), { target: { files: [refFile] } });
    expect(uploadReferenceFile).toHaveBeenCalledWith(refFile);

    fireEvent.change(screen.getByLabelText(/BAM file/, { selector: 'input' }), { target: { files: [bamFile] } });
    expect(uploadBamFile).toHaveBeenCalledWith(bamFile);

    rerender(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'fasta',
          uploadFastaFile,
        })}
      />,
    );
    const fastaFile = new File(['>s\nACGT'], 's.fasta');
    fireEvent.change(screen.getByLabelText(/FASTA file/, { selector: 'input' }), { target: { files: [fastaFile] } });
    expect(uploadFastaFile).toHaveBeenCalledWith(fastaFile);

    rerender(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'regenerate',
          uploadJsonFile,
        })}
      />,
    );
    const jsonFile = new File(['{}'], 'r.json');
    fireEvent.change(screen.getByLabelText(/Results JSON/, { selector: 'input' }), { target: { files: [jsonFile] } });
    expect(uploadJsonFile).toHaveBeenCalledWith(jsonFile);
  });

  it('gates submit on isUploading and partial IDs, invokes submit handlers, and toggles database label', () => {
    const runSelectedProfile = vi.fn();
    const runRegenerateFromJson = vi.fn();

    const { rerender } = render(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'vcf',
          vcfInput: { vcf_id: 'v1', reference_id: 'r1', input_display_name: 'a.vcf', bam_id: null, sample: '', min_af: 0.01, min_depth: 10 },
          isUploading: true,
          runSelectedProfile,
        })}
      />,
    );
    expect(screen.getByRole('button', { name: 'Analyze' })).toBeDisabled();
    expect(screen.getByText('using HSVdb')).toBeInTheDocument();

    // Partial VCF inputs disable Analyze.
    rerender(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'vcf',
          vcfInput: { vcf_id: 'v1', reference_id: '', input_display_name: 'a.vcf', bam_id: null, sample: '', min_af: 0.01, min_depth: 10 },
          isUploading: false,
          runSelectedProfile,
        })}
      />,
    );
    expect(screen.getByRole('button', { name: 'Analyze' })).toBeDisabled();

    rerender(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'vcf',
          vcfInput: { vcf_id: '', reference_id: 'r1', input_display_name: '', bam_id: null, sample: '', min_af: 0.01, min_depth: 10 },
          isUploading: false,
          runSelectedProfile,
        })}
      />,
    );
    expect(screen.getByRole('button', { name: 'Analyze' })).toBeDisabled();

    // Complete VCF inputs enable Analyze and invoke runSelectedProfile.
    rerender(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'vcf',
          vcfInput: { vcf_id: 'v1', reference_id: 'r1', input_display_name: 'a.vcf', bam_id: null, sample: '', min_af: 0.01, min_depth: 10 },
          isUploading: false,
          runSelectedProfile,
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Analyze' }));
    expect(runSelectedProfile).toHaveBeenCalledTimes(1);

    // FASTA submit click + hidden "using <db>" when busy.
    rerender(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'fasta',
          fastaInput: { fasta_id: 'f1', input_display_name: 'a.fasta', sample: '' },
          runSelectedProfile,
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Analyze' }));
    expect(runSelectedProfile).toHaveBeenCalledTimes(2);

    rerender(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'regenerate',
          jsonInputId: 'j1',
          isProfileBusy: true,
          runRegenerateFromJson,
        })}
      />,
    );
    expect(screen.queryByText(/using HSVdb/)).not.toBeInTheDocument();

    rerender(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'regenerate',
          jsonInputId: 'j1',
          isProfileBusy: false,
          isRegenerateBusy: false,
          runRegenerateFromJson,
        })}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }));
    expect(runRegenerateFromJson).toHaveBeenCalledTimes(1);
  });

  it('opens report/artifact URLs in new tab with noopener,noreferrer, disables missing downloads, and toggles placeholder', () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);

    const { rerender } = render(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          inlineReportPath: '',
          selectedProfileReportPath: 'rep-1',
          reportOptions: [
            { path: 'rep-1', label: 'Sample 1', pdfPath: 'pdf-1', jsonPath: '', tsvPath: 'tsv-1' },
          ],
        })}
      />,
    );

    expect(document.querySelector('.report-placeholder')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download JSON' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Open in new tab' }));
    expect(openSpy).toHaveBeenCalledWith('/api/report?artifact_id=rep-1', '_blank', 'noopener,noreferrer');

    fireEvent.click(screen.getByRole('button', { name: 'Download PDF' }));
    expect(openSpy).toHaveBeenCalledWith('/api/artifact?artifact_id=pdf-1', '_blank', 'noopener,noreferrer');

    fireEvent.click(screen.getByRole('button', { name: 'Download TSV' }));
    expect(openSpy).toHaveBeenCalledWith('/api/artifact?artifact_id=tsv-1', '_blank', 'noopener,noreferrer');

    rerender(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          inlineReportPath: 'rep-1',
          selectedProfileReportPath: '',
          reportOptions: [
            { path: 'rep-1', label: 'Sample 1', pdfPath: '', jsonPath: '', tsvPath: '' },
          ],
        })}
      />,
    );
    expect(document.querySelector('.report-placeholder')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open in new tab' })).toBeDisabled();
  });

  it('resets file input value on selection so re-selecting the same filename triggers onChange', () => {
    const uploadVcfFile = vi.fn();
    render(
      <SingleAnalyzePanel
        logic={makePanelLogic({
          activeProfileMode: 'vcf',
          uploadVcfFile,
        })}
      />,
    );

    const vcfInput = document.querySelector('[data-tour-target="vcf-file"] input[type="file"]');
    const file = new File(['##VCF'], 'sample1.vcf', { type: 'text/plain' });
    let currentValue = 'C:\\fakepath\\sample1.vcf';
    Object.defineProperty(vcfInput, 'value', {
      configurable: true,
      get: () => currentValue,
      set: (next) => {
        currentValue = next;
      },
    });

    fireEvent.change(vcfInput, { target: { files: [file] } });
    expect(uploadVcfFile).toHaveBeenCalledTimes(1);
    expect(vcfInput.value).toBe('');

    currentValue = 'C:\\fakepath\\sample1.vcf';
    fireEvent.change(vcfInput, { target: { files: [file] } });
    expect(uploadVcfFile).toHaveBeenCalledTimes(2);
    expect(vcfInput.value).toBe('');
  });
});
