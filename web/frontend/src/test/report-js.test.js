import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it, vi } from 'vitest';

const REPORT_JS_PATH = path.resolve(
  import.meta.dirname,
  '../../../../respro/report/static/report.js',
);

describe('standalone HTML report.js regressions', () => {
  it('wires faceted tables without a mutation table, keeps unassigned classes visible, and sanitizes TSV downloads', async () => {
    document.body.innerHTML = `
      <div id="tab-summary">
        <button class="summary-drug-download-button">Download</button>
        <table class="drug-interp-table">
          <thead><tr><th>Drug</th><th>Assessment</th></tr></thead>
          <tbody>
            <tr>
              <td>DrugA</td>
              <td>Resistant <span class="db-hit-freq-info"><div class="db-hit-info-panel">Thresholds applied: x=1</div></span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div id="tab-database-hits">
        <div role="region">
          <input id="db-hit-search-input">
          <details class="mutation-filter-menu" data-filter-group="db-hit-drug-class"><summary>Class</summary><div class="mutation-filter-options"></div></details>
          <details class="mutation-filter-menu" data-filter-group="db-hit-drug"><summary>Drug</summary><div class="mutation-filter-options"></div></details>
          <details class="mutation-filter-menu" data-filter-group="db-hit-freq"><summary>Freq</summary><div class="mutation-filter-options"></div></details>
          <button class="db-hit-reset-button"></button>
          <button class="db-hit-download-button"></button>
        </div>
        <table class="db-hit-table">
          <thead><tr><th class="db-hit-drug-class-th">Drug class</th><th>Drug</th><th>Mutations</th><th>Reference</th><th>Data</th><th>Freq</th><th>Source</th></tr></thead>
          <tbody>
            <tr class="db-hit-row" data-drug-class-value="NRTI" data-drug-value="DrugA" data-freq-value="high">
              <td class="db-hit-drug-class-cell">NRTI</td><td class="db-hit-drug-cell">DrugA</td>
              <td><div class="db-hit-mut-group">pol: M184V</div></td><td class="db-hit-reference-cell">Ref1</td>
              <td><span class="db-hit-metric">Phenotype: resistant</span></td><td>high</td><td class="db-hit-source-cell">DB</td>
            </tr>
            <tr class="db-hit-row" data-drug-class-value="" data-drug-value="DrugB" data-freq-value="low">
              <td class="db-hit-drug-class-cell">—</td><td class="db-hit-drug-cell">DrugB</td>
              <td><div class="db-hit-mut-group">pol: K65R</div></td><td class="db-hit-reference-cell">Ref2</td>
              <td><span class="db-hit-metric">Phenotype: resistant</span></td><td>low</td><td class="db-hit-source-cell">DB</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div id="tab-all-mutations"><p class="tab-empty-state">No CDS variants.</p></div>
    `;

    const downloads = [];
    const origCreateObjectURL = URL.createObjectURL;
    const origRevokeObjectURL = URL.revokeObjectURL;
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
      downloads[downloads.length - 1].name = this.download;
    });
    URL.createObjectURL = (blob) => {
      downloads.push({ blob, name: '' });
      return 'blob:test';
    };
    URL.revokeObjectURL = () => {};

    try {
      const script = fs.readFileSync(REPORT_JS_PATH, 'utf8');
      window.eval(script);
      document.dispatchEvent(new Event('DOMContentLoaded'));

      const rows = Array.from(document.querySelectorAll('.db-hit-row'));
      expect(rows.map((r) => r.hidden)).toEqual([false, false]);

      // Uncheck all in drug-class hides all rows.
      const toggle = document.querySelector('[data-filter-group="db-hit-drug-class"] .mutation-filter-toggle-all');
      toggle.click();
      expect(rows.map((r) => r.hidden)).toEqual([true, true]);

      // Re-checking Unassigned recovers cascade and displays DrugB only.
      const unassigned = Array.from(
        document.querySelectorAll('[data-filter-group="db-hit-drug-class"] .mutation-filter-option'),
      ).find((cb) => cb.value === '');
      expect(unassigned).toBeTruthy();
      unassigned.checked = true;
      unassigned.dispatchEvent(new Event('change'));
      expect(rows.map((r) => r.hidden)).toEqual([true, false]);

      // Reset restores all rows and TSV downloads include Reference and strip tooltip text.
      document.querySelector('.db-hit-reset-button').click();
      expect(rows.map((r) => r.hidden)).toEqual([false, false]);

      document.querySelector('.db-hit-download-button').click();
      document.querySelector('.summary-drug-download-button').click();

      const texts = {};
      for (const item of downloads) {
        texts[item.name] = await item.blob.text();
      }
      expect(texts['database_hits.tsv'].split('\n')[0]).toContain('Reference');
      expect(texts['database_hits.tsv']).toContain('Ref1');
      expect(texts['database_hits.tsv']).toContain('Ref2');
      expect(texts['drug_interpretation.tsv']).not.toContain('Thresholds applied');
      expect(texts['drug_interpretation.tsv']).toContain('DrugA\tResistant');
    } finally {
      clickSpy.mockRestore();
      URL.createObjectURL = origCreateObjectURL;
      URL.revokeObjectURL = origRevokeObjectURL;
    }
  });
});
