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
    expect(steps.length).toBeGreaterThanOrEqual(15);

    // Spot-check the required step ids are present in order.
    const ids = steps.map((s) => s.id);
    expect(ids).toContain('database-selector');
    expect(ids).toContain('sidebar-rail');
    expect(ids).toContain('analyze-submode');
    expect(ids).toContain('vcf-mode');
    expect(ids).toContain('fasta-mode');
    expect(ids).toContain('regenerate-mode');
    expect(ids).toContain('analyze-button');
    expect(ids).toContain('previous-reports');
    expect(ids).toContain('batch-mode');
    expect(ids).toContain('reports-table');
    expect(ids).toContain('comparison-heatmap');
    expect(ids).toContain('database-dashboard');
    expect(ids).toContain('browse-mutations');
    expect(ids).toContain('about');
    expect(ids).toContain('docs-handoff');

    // The docs-handoff step must be last.
    expect(ids[ids.length - 1]).toBe('docs-handoff');
  });

  it('every step has a non-empty id, targetSelector, title, and body', () => {
    const steps = buildTourSteps({
      setActiveMode: vi.fn(),
      setActiveProfileMode: vi.fn(),
      setAnalyzeSubMode: vi.fn(),
    });
    for (const step of steps) {
      expect(step.id).toBeTruthy();
      expect(step.targetSelector).toBeTruthy();
      expect(typeof step.targetSelector).toBe('string');
      expect(step.title).toBeTruthy();
      expect(step.body).toBeTruthy();
      expect(typeof step.body).toBe('string');
    }
  });

  it('the VCF step body mentions BAM and coverage and the cutoffs', () => {
    const steps = buildTourSteps({ setActiveMode: vi.fn(), setActiveProfileMode: vi.fn(), setAnalyzeSubMode: vi.fn() });
    const vcf = steps.find((s) => s.id === 'vcf-mode');
    expect(vcf.body.toLowerCase()).toContain('bam');
    expect(vcf.body.toLowerCase()).toContain('coverage');
    expect(vcf.body.toLowerCase()).toContain('frequency cutoff');
    expect(vcf.body.toLowerCase()).toContain('coverage cutoff');
  });

  it('the batch step body mentions the 25-per-batch-and-minute rate limit', () => {
    const steps = buildTourSteps({ setActiveMode: vi.fn(), setActiveProfileMode: vi.fn(), setAnalyzeSubMode: vi.fn() });
    const batch = steps.find((s) => s.id === 'batch-mode');
    expect(batch.body).toContain('25');
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

    it('the vcf-mode step calls setActiveMode, setAnalyzeSubMode("single"), and setActiveProfileMode("vcf")', () => {
      const setActiveMode = vi.fn();
      const setActiveProfileMode = vi.fn();
      const setAnalyzeSubMode = vi.fn();
      const steps = buildTourSteps({ setActiveMode, setActiveProfileMode, setAnalyzeSubMode });
      steps.find((s) => s.id === 'vcf-mode').before();
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

    it('the batch-mode step sets sub-mode to batch', () => {
      const setAnalyzeSubMode = vi.fn();
      const steps = buildTourSteps({ setActiveMode: vi.fn(), setActiveProfileMode: vi.fn(), setAnalyzeSubMode });
      steps.find((s) => s.id === 'batch-mode').before();
      expect(setAnalyzeSubMode).toHaveBeenCalledWith('batch');
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
