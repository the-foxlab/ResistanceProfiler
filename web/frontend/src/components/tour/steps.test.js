import { describe, it, expect, vi } from 'vitest';

import { buildTourSteps, TOUR_DOCS_URL, TOUR_DOCS_OUTPUT_URL } from './steps';

describe('buildTourSteps', () => {
  it('returns an array with at least 15 steps in the required order', () => {
    const steps = buildTourSteps({
      setActiveMode: vi.fn(),
      setActiveProfileMode: vi.fn(),
      setAnalyzeSubMode: vi.fn(),
    });
    expect(Array.isArray(steps)).toBe(true);
    expect(steps.length).toBe(17);

    // Spot-check the required step ids are present in order.
    const ids = steps.map((s) => s.id);
    expect(ids).toEqual([
      'database-selector',
      'vcf-file',
      'vcf-reference',
      'vcf-bam',
      'vcf-sample-name',
      'vcf-frequency-cutoff',
      'vcf-coverage-cutoff',
      'fasta-mode',
      'regenerate-mode',
      'analyze-button',
      'previous-reports',
      'reports-table',
      'comparison-heatmap',
      'database-dashboard',
      'browse-mutations',
      'about',
      'docs-handoff',
    ]);

    // The docs-handoff step must be last.
    expect(ids[ids.length - 1]).toBe('docs-handoff');
  });

  it('every step has a non-empty id, title, and body; targetSelector is a string or null', () => {
    const steps = buildTourSteps({
      setActiveMode: vi.fn(),
      setActiveProfileMode: vi.fn(),
      setAnalyzeSubMode: vi.fn(),
    });
    for (const step of steps) {
      expect(step.id).toBeTruthy();
      expect(step.targetSelector === null || typeof step.targetSelector === 'string').toBe(true);
      expect(step.title).toBeTruthy();
      expect(step.body).toBeTruthy();
      expect(typeof step.body).toBe('string');
    }
  });

  it('the vcf-bam step body mentions BAM and coverage', () => {
    const steps = buildTourSteps({ setActiveMode: vi.fn(), setActiveProfileMode: vi.fn(), setAnalyzeSubMode: vi.fn() });
    const bam = steps.find((s) => s.id === 'vcf-bam');
    expect(bam.body.toLowerCase()).toContain('bam');
    expect(bam.body.toLowerCase()).toContain('coverage');
  });

  it('the vcf-frequency-cutoff step body mentions allele frequency', () => {
    const steps = buildTourSteps({ setActiveMode: vi.fn(), setActiveProfileMode: vi.fn(), setAnalyzeSubMode: vi.fn() });
    const freq = steps.find((s) => s.id === 'vcf-frequency-cutoff');
    expect(freq.body.toLowerCase()).toContain('allele frequency');
  });

  it('the vcf-coverage-cutoff step body mentions read depth', () => {
    const steps = buildTourSteps({ setActiveMode: vi.fn(), setActiveProfileMode: vi.fn(), setAnalyzeSubMode: vi.fn() });
    const cov = steps.find((s) => s.id === 'vcf-coverage-cutoff');
    expect(cov.body.toLowerCase()).toContain('read depth');
  });

  it('the comparison step body mentions Select all comparable, Compare selected, Non-synonymous only, DB hits only, and heatmap', () => {
    const steps = buildTourSteps({ setActiveMode: vi.fn(), setActiveProfileMode: vi.fn(), setAnalyzeSubMode: vi.fn() });
    const comp = steps.find((s) => s.id === 'comparison-heatmap');
    expect(comp.body).toContain('Select all comparable');
    expect(comp.body).toContain('Compare selected');
    expect(comp.body).toContain('Non-synonymous only');
    expect(comp.body).toContain('DB hits only');
    expect(comp.body.toLowerCase()).toContain('heatmap');
  });

  it('the final step links to the official GitHub docs', () => {
    const steps = buildTourSteps({ setActiveMode: vi.fn(), setActiveProfileMode: vi.fn(), setAnalyzeSubMode: vi.fn() });
    const last = steps[steps.length - 1];
    expect(last.link).toBeDefined();
    expect(last.link.href).toBe(TOUR_DOCS_OUTPUT_URL);
    expect(TOUR_DOCS_OUTPUT_URL).toContain('the-foxlab.github.io/ResistanceProfiler');
  });

  describe('before hooks drive navigation', () => {
    it('the database-selector step calls setActiveMode("analyze")', () => {
      const setActiveMode = vi.fn();
      const steps = buildTourSteps({ setActiveMode, setActiveProfileMode: vi.fn(), setAnalyzeSubMode: vi.fn() });
      steps.find((s) => s.id === 'database-selector').before();
      expect(setActiveMode).toHaveBeenCalledWith('analyze');
    });

    it('the vcf-file step calls setActiveMode, setAnalyzeSubMode("single"), and setActiveProfileMode("vcf")', () => {
      const setActiveMode = vi.fn();
      const setActiveProfileMode = vi.fn();
      const setAnalyzeSubMode = vi.fn();
      const steps = buildTourSteps({ setActiveMode, setActiveProfileMode, setAnalyzeSubMode });
      steps.find((s) => s.id === 'vcf-file').before();
      expect(setActiveMode).toHaveBeenCalledWith('analyze');
      expect(setAnalyzeSubMode).toHaveBeenCalledWith('single');
      expect(setActiveProfileMode).toHaveBeenCalledWith('vcf');
    });

    it('the fasta-mode step sets profile mode to fasta', () => {
      const setActiveProfileMode = vi.fn();
      const steps = buildTourSteps({ setActiveMode: vi.fn(), setActiveProfileMode, setAnalyzeSubMode: vi.fn() });
      steps.find((s) => s.id === 'fasta-mode').before();
      expect(setActiveProfileMode).toHaveBeenCalledWith('fasta');
    });

    it('the regenerate-mode step sets profile mode to regenerate', () => {
      const setActiveProfileMode = vi.fn();
      const steps = buildTourSteps({ setActiveMode: vi.fn(), setActiveProfileMode, setAnalyzeSubMode: vi.fn() });
      steps.find((s) => s.id === 'regenerate-mode').before();
      expect(setActiveProfileMode).toHaveBeenCalledWith('regenerate');
    });

    it('the reports-table step calls setActiveMode("results")', () => {
      const setActiveMode = vi.fn();
      const steps = buildTourSteps({ setActiveMode, setActiveProfileMode: vi.fn(), setAnalyzeSubMode: vi.fn() });
      steps.find((s) => s.id === 'reports-table').before();
      expect(setActiveMode).toHaveBeenCalledWith('results');
    });

    it('the database-dashboard step calls setActiveMode("database")', () => {
      const setActiveMode = vi.fn();
      const steps = buildTourSteps({ setActiveMode, setActiveProfileMode: vi.fn(), setAnalyzeSubMode: vi.fn() });
      steps.find((s) => s.id === 'database-dashboard').before();
      expect(setActiveMode).toHaveBeenCalledWith('database');
    });
  });
});
