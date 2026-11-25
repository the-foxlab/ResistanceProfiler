import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it, vi } from 'vitest';

const REPORT_JS_PATH = path.resolve(
  import.meta.dirname,
  '../../../../respro/report/static/report.js',
);

async function withReportDomCleanup(fn) {
  const docListeners = [];
  const winListeners = [];
  const origDocAdd = document.addEventListener.bind(document);
  const origWinAdd = window.addEventListener.bind(window);
  const docSpy = vi.spyOn(document, 'addEventListener').mockImplementation((type, listener, opts) => {
    docListeners.push([type, listener, opts]);
    return origDocAdd(type, listener, opts);
  });
  const winSpy = vi.spyOn(window, 'addEventListener').mockImplementation((type, listener, opts) => {
    winListeners.push([type, listener, opts]);
    return origWinAdd(type, listener, opts);
  });
  try {
    return await fn();
  } finally {
    docSpy.mockRestore();
    winSpy.mockRestore();
    for (const [type, listener, opts] of docListeners) {
      document.removeEventListener(type, listener, opts);
    }
    for (const [type, listener, opts] of winListeners) {
      window.removeEventListener(type, listener, opts);
    }
  }
}

describe('standalone HTML report.js regressions', () => {
  it('wires faceted tables without a mutation table, keeps unassigned classes visible, and sanitizes TSV downloads', async () => {
    document.body.innerHTML = `
      <div id="tab-summary">
        <button class="summary-drug-download-button">Download</button>
        <table class="drug-interp-table">
          <thead><tr><th>Drug</th><th>Assessment</th></tr></thead>
          <tbody>
            <tr class="drug-group-header-row"><td colspan="2">NRTI</td></tr>
            <tr data-drug-class="NRTI">
              <td>DrugA</td>
              <td>Resistant <span class="db-hit-freq-info"><div class="db-hit-info-panel">Thresholds applied: x=1</div></span></td>
            </tr>
            <tr data-drug-class="">
              <td>DrugUngrouped</td>
              <td>Susceptible</td>
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
      await withReportDomCleanup(async () => {
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
        expect(texts['drug_interpretation.tsv']).toContain('NRTI\tDrugA\tResistant');
        expect(texts['drug_interpretation.tsv']).toContain('\n\tDrugUngrouped\tSusceptible');
        expect(texts['drug_interpretation.tsv']).not.toContain('NRTI\tDrugUngrouped');
      });
    } finally {
      clickSpy.mockRestore();
      URL.createObjectURL = origCreateObjectURL;
      URL.revokeObjectURL = origRevokeObjectURL;
    }
  });

  it('supports opaque-origin ("null") embedded iframe postMessage bridge', async () => {
    document.documentElement.removeAttribute('data-theme');
    document.body.innerHTML = `
      <div id="plot-modal">
        <button id="plot-modal-open" type="button">Expand</button>
        <button id="plot-modal-close" type="button">Close</button>
        <div id="plot-modal-backdrop"></div>
        <img class="plot-modal-image" src="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=" alt="Resistance plot" />
      </div>
      <button
        type="button"
        class="drug-structure-button"
        data-structure-url="https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/135398513/PNG"
        data-drug-title="Acyclovir"
      >Structure</button>
      <div id="drug-structure-modal">
        <div id="drug-structure-modal-backdrop"></div>
        <div id="drug-structure-modal-title"></div>
        <img id="drug-structure-modal-img" />
      </div>
      <button
        type="button"
        class="feature-sequence-button"
        data-nt-sequence="ATGGCC"
        data-aa-sequence="MA"
        data-feature-title="UL23"
      >Sequence</button>
      <div id="feature-sequence-modal">
        <div id="feature-sequence-modal-backdrop"></div>
        <div id="feature-sequence-modal-title"></div>
        <pre id="feature-sequence-modal-sequence"></pre>
      </div>
    `;

    const parentPostMessage = vi.fn();
    const fakeParent = { postMessage: parentPostMessage };
    const origParentDescriptor = Object.getOwnPropertyDescriptor(window, 'parent');
    const origResizeObserver = window.ResizeObserver;
    const origScrollHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight');

    Object.defineProperty(window, 'parent', { configurable: true, value: fakeParent });
    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', { configurable: true, value: 480 });
    window.ResizeObserver = class {
      observe() {}
      disconnect() {}
    };

    try {
      await withReportDomCleanup(() => {
        const script = fs.readFileSync(REPORT_JS_PATH, 'utf8');
        window.eval(script);
        document.dispatchEvent(new Event('DOMContentLoaded'));

        // Initial height post uses '*' so opaque 'null' origin does not throw.
        expect(parentPostMessage).toHaveBeenCalledWith(
          { type: 'respro:report-height', height: 480 },
          '*',
        );

        // Theme messages from a foreign source are ignored, while window.parent is accepted
        // even when event.origin differs from opaque window.location.origin.
        window.dispatchEvent(new MessageEvent('message', {
          data: { type: 'respro:report-theme', theme: 'dark' },
          origin: 'http://127.0.0.1:5173',
          source: window,
        }));
        expect(document.documentElement.getAttribute('data-theme')).not.toBe('dark');

        window.dispatchEvent(new MessageEvent('message', {
          data: { type: 'respro:report-theme', theme: 'dark' },
          origin: 'http://127.0.0.1:5173',
          source: fakeParent,
        }));
        expect(document.documentElement.getAttribute('data-theme')).toBe('dark');

        // Delegated modals post with targetOrigin '*'.
        document.getElementById('plot-modal-open').click();
        expect(parentPostMessage).toHaveBeenCalledWith(
          {
            type: 'respro:open-plot',
            src: 'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=',
            alt: 'Resistance plot',
          },
          '*',
        );

        document.querySelector('.feature-sequence-button').click();
        expect(parentPostMessage).toHaveBeenCalledWith(
          {
            type: 'respro:open-sequence',
            title: 'UL23',
            ntSequence: 'ATGGCC',
            aaSequence: 'MA',
          },
          '*',
        );

        document.querySelector('.drug-structure-button').click();
        expect(parentPostMessage).toHaveBeenCalledWith(
          {
            type: 'respro:open-structure',
            src: 'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/135398513/PNG',
            title: 'Acyclovir',
          },
          '*',
        );
      });
    } finally {
      if (origParentDescriptor) {
        Object.defineProperty(window, 'parent', origParentDescriptor);
      }
      if (origScrollHeight) {
        Object.defineProperty(HTMLElement.prototype, 'scrollHeight', origScrollHeight);
      } else {
        delete HTMLElement.prototype.scrollHeight;
      }
      window.ResizeObserver = origResizeObserver;
    }
  });

  it('neutralizes TSV formula triggers, preserves "0", sanitizes headers, and defers URL.revokeObjectURL across all 4 report exports', async () => {
    vi.useFakeTimers();
    document.body.innerHTML = `
      <div id="tab-all-mutations">
        <div class="mutation-toolbar">
          <input id="mutation-search-input">
          <details class="mutation-filter-menu" data-filter-group="feature"><summary>Feature</summary><div class="mutation-filter-options"></div></details>
          <details class="mutation-filter-menu" data-filter-group="consequence"><summary>Consequence</summary><div class="mutation-filter-options"></div></details>
          <details class="mutation-filter-menu" data-filter-group="database"><summary>In Database</summary><div class="mutation-filter-options"></div></details>
          <button class="mutation-reset-button"></button>
          <button class="mutation-download-button"></button>
        </div>
        <table class="mutation-table">
          <thead>
            <tr>
              <th>Feature</th><th>NT change stored reference</th><th>AA change</th><th>Consequence</th><th>Variant frequency</th><th>In database</th>
            </tr>
          </thead>
          <tbody>
            <tr class="mutation-row" data-feature-value="=cmd|'/C calc'!A0" data-consequence-value="-1" data-database-values="=DB">
              <td class="mutation-feature-cell">=cmd|'/C calc'!A0</td>
              <td class="mutation-nt">+1A>G</td>
              <td class="mutation-aa">@SUM(1,1)</td>
              <td class="mutation-consequence">-1</td>
              <td class="mutation-freq">0</td>
              <td>=DB</td>
            </tr>
            <tr class="mutation-row" data-feature-value="tab" data-consequence-value="missense" data-database-values="None">
              <td class="mutation-feature-cell">\t=tabbed</td>
              <td class="mutation-nt">\r=cr</td>
              <td class="mutation-aa">M184V</td>
              <td class="mutation-consequence">missense</td>
              <td class="mutation-freq">0.5</td>
              <td>None</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div id="tab-database-hits">
        <div role="region">
          <input id="db-hit-search-input">
          <button class="db-hit-reset-button"></button>
          <button class="db-hit-download-button"></button>
        </div>
        <table class="db-hit-table">
          <thead><tr><th>Drug</th><th>Mutations</th><th>Data</th><th>Freq</th><th>Source</th></tr></thead>
          <tbody>
            <tr class="db-hit-row" data-drug-class-value="" data-drug-value="=D1" data-freq-value="0">
              <td class="db-hit-drug-cell">=D1</td>
              <td><div class="db-hit-mut-group">+M184V</div></td>
              <td><span class="db-hit-metric">-inf</span></td>
              <td>0</td>
              <td class="db-hit-source-cell">@SRC</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div id="tab-similarity">
        <div role="region">
          <input id="sim-search-input">
          <button class="sim-reset-button"></button>
          <button class="sim-download-button"></button>
        </div>
        <table class="sim-table">
          <thead><tr><th>Drug</th><th>Mutation</th><th>Known Rule</th><th>Similarity</th><th>Data</th><th>Freq</th><th>Source</th></tr></thead>
          <tbody>
            <tr class="sim-row" data-sim-drug-class-value="" data-sim-drug-value="@DRUG" data-sim-freq-value="0">
              <td class="sim-drug-cell">@DRUG</td>
              <td class="sim-mutation-cell">-M184V</td>
              <td class="sim-rule-cell">+K65R</td>
              <td><span class="sim-badge">0</span></td>
              <td><span class="db-hit-metric">=METRIC</span></td>
              <td>0</td>
              <td class="sim-source-cell">=SRC</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div id="tab-summary">
        <button class="summary-drug-download-button">Download</button>
        <table class="drug-interp-table">
          <thead><tr><th>=HeaderInject</th><th>Assessment</th></tr></thead>
          <tbody>
            <tr>
              <td>=HYPERLINK("http://evil.example","Click")</td>
              <td>+1+1</td>
            </tr>
            <tr>
              <td>0</td>
              <td>-0</td>
            </tr>
          </tbody>
        </table>
      </div>
    `;

    const downloads = [];
    const origCreateObjectURL = URL.createObjectURL;
    const origRevokeObjectURL = URL.revokeObjectURL;
    const revokeSpy = vi.fn();
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
      downloads[downloads.length - 1].name = this.download;
    });
    URL.createObjectURL = (blob) => {
      downloads.push({ blob, name: '' });
      return `blob:tsv-${downloads.length}`;
    };
    URL.revokeObjectURL = revokeSpy;

    try {
      await withReportDomCleanup(async () => {
        const script = fs.readFileSync(REPORT_JS_PATH, 'utf8');
        window.eval(script);
        document.dispatchEvent(new Event('DOMContentLoaded'));

        document.querySelector('.mutation-download-button').click();
        document.querySelector('.db-hit-download-button').click();
        document.querySelector('.sim-download-button').click();
        document.querySelector('.summary-drug-download-button').click();

        // URL.revokeObjectURL must be deferred by 1000ms (WA-050), not called synchronously.
        expect(revokeSpy).not.toHaveBeenCalled();
        vi.advanceTimersByTime(1000);
        expect(revokeSpy).toHaveBeenCalledTimes(4);

        const texts = {};
        for (const item of downloads) {
          texts[item.name] = await item.blob.text();
        }

        expect(texts['variant_profile.tsv']).toContain(
          "'=cmd|'/C calc'!A0\t'+1A>G\t'@SUM(1,1)\t'-1\t0\t'=DB",
        );
        expect(texts['variant_profile.tsv']).toContain("'=tabbed\t'=cr\tM184V\tmissense\t0.5\tNone");
        expect(texts['database_hits.tsv']).toContain("'=D1\t'+M184V\t'-inf\t0\t'@SRC");
        expect(texts['similarity_entries.tsv']).toContain(
          "'@DRUG\t'-M184V\t'+K65R\t0\t'=METRIC\t0\t'=SRC",
        );
        expect(texts['drug_interpretation.tsv']).toBe(
          "'=HeaderInject\tAssessment\n'=HYPERLINK(\"http://evil.example\",\"Click\")\t'+1+1\n0\t'-0\n",
        );
      });
    } finally {
      vi.useRealTimers();
      clickSpy.mockRestore();
      URL.createObjectURL = origCreateObjectURL;
      URL.revokeObjectURL = origRevokeObjectURL;
    }
  });
});
