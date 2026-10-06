import { describe, it, expect, vi } from 'vitest';

import { buildTourSteps, TOUR_DOCS_URL, TOUR_DOCS_OUTPUT_URL } from './steps';

function makeNav() {
  return {
    navigate: vi.fn(),
    setActiveProfileMode: vi.fn(),
    setAnalyzeSubMode: vi.fn(),
  };
}

describe('buildTourSteps', () => {
  it('returns an array with the required steps in order', () => {
    const steps = buildTourSteps(makeNav());
    expect(Array.isArray(steps)).toBe(true);
    expect(steps.length).toBe(18);

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
      'analyze-submode',
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
    const steps = buildTourSteps(makeNav());
    for (const step of steps) {
      expect(step.id).toBeTruthy();
      expect(step.targetSelector === null || typeof step.targetSelector === 'string').toBe(true);
      expect(step.title).toBeTruthy();
      expect(step.body).toBeTruthy();
      expect(typeof step.body).toBe('string');
    }
  });

  it('the vcf-bam step body mentions BAM and coverage', () => {
    const steps = buildTourSteps(makeNav());
    const bam = steps.find((s) => s.id === 'vcf-bam');
    expect(bam.body.toLowerCase()).toContain('bam');
    expect(bam.body.toLowerCase()).toContain('coverage');
  });

  it('the vcf-frequency-cutoff step body mentions allele frequency', () => {
    const steps = buildTourSteps(makeNav());
    const freq = steps.find((s) => s.id === 'vcf-frequency-cutoff');
    expect(freq.body.toLowerCase()).toContain('allele frequency');
  });

  it('the vcf-coverage-cutoff step body mentions read depth', () => {
    const steps = buildTourSteps(makeNav());
    const cov = steps.find((s) => s.id === 'vcf-coverage-cutoff');
    expect(cov.body.toLowerCase()).toContain('read depth');
  });

  it('the comparison step body mentions heatmap', () => {
    const steps = buildTourSteps(makeNav());
    const comp = steps.find((s) => s.id === 'comparison-heatmap');
    expect(comp.body.toLowerCase()).toContain('heatmap');
  });

  it('the final step links to the official GitHub docs', () => {
    const steps = buildTourSteps(makeNav());
    const last = steps[steps.length - 1];
    expect(last.link).toBeDefined();
    expect(last.link.href).toBe(TOUR_DOCS_OUTPUT_URL);
    expect(TOUR_DOCS_OUTPUT_URL).toContain('the-foxlab.github.io/ResistanceProfiler');
  });

  describe('before hooks drive route navigation', () => {
    it('the database-selector step navigates to /analysis', () => {
      const nav = makeNav();
      const steps = buildTourSteps(nav);
      steps.find((s) => s.id === 'database-selector').before();
      expect(nav.navigate).toHaveBeenCalledWith('/analysis');
    });

    it('the vcf-file step navigates to /analysis and sets profile substate', () => {
      const nav = makeNav();
      const steps = buildTourSteps(nav);
      steps.find((s) => s.id === 'vcf-file').before();
      expect(nav.navigate).toHaveBeenCalledWith('/analysis');
      expect(nav.setAnalyzeSubMode).toHaveBeenCalledWith('single');
      expect(nav.setActiveProfileMode).toHaveBeenCalledWith('vcf');
    });

    it('the fasta-mode step sets profile mode to fasta', () => {
      const nav = makeNav();
      const steps = buildTourSteps(nav);
      steps.find((s) => s.id === 'fasta-mode').before();
      expect(nav.setActiveProfileMode).toHaveBeenCalledWith('fasta');
    });

    it('the regenerate-mode step sets profile mode to regenerate', () => {
      const nav = makeNav();
      const steps = buildTourSteps(nav);
      steps.find((s) => s.id === 'regenerate-mode').before();
      expect(nav.setActiveProfileMode).toHaveBeenCalledWith('regenerate');
    });

    it('the reports-table step navigates to /analysis/reports', () => {
      const nav = makeNav();
      const steps = buildTourSteps(nav);
      steps.find((s) => s.id === 'reports-table').before();
      expect(nav.navigate).toHaveBeenCalledWith('/analysis/reports');
    });

    it('the database-dashboard step navigates to /databases', () => {
      const nav = makeNav();
      const steps = buildTourSteps(nav);
      steps.find((s) => s.id === 'database-dashboard').before();
      expect(nav.navigate).toHaveBeenCalledWith('/databases');
    });

    it('the browse-mutations step navigates to /databases/mutations', () => {
      const nav = makeNav();
      const steps = buildTourSteps(nav);
      steps.find((s) => s.id === 'browse-mutations').before();
      expect(nav.navigate).toHaveBeenCalledWith('/databases/mutations');
    });

    it('the about step navigates to /about', () => {
      const nav = makeNav();
      const steps = buildTourSteps(nav);
      steps.find((s) => s.id === 'about').before();
      expect(nav.navigate).toHaveBeenCalledWith('/about');
    });
  });
});
