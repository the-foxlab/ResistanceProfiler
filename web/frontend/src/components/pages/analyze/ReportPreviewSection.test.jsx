import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';

import { ReportPreviewSection, isAllowedReportAssetSrc } from './ReportPreviewSection';
import { ThemeContext } from '../../../hooks/useTheme';

function renderPreview(props = {}, theme = 'light') {
  return render(
    <ThemeContext.Provider value={theme}>
      <ReportPreviewSection
        showFrame
        inlineReportPath="rep-1"
        buildReportUrl={(id) => `/api/report?artifact_id=${id}`}
        {...props}
      />
    </ThemeContext.Provider>,
  );
}

function dispatchMessage({ data, origin = 'null', source }) {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { data, origin, source }));
  });
}

describe('ReportPreviewSection security boundaries', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('sandboxes the report iframe without allow-same-origin and posts theme on load', () => {
    renderPreview({}, 'dark');
    const frame = document.querySelector('iframe.workspace-frame');
    expect(frame).not.toBeNull();
    const sandboxTokens = frame.getAttribute('sandbox').split(/\s+/);
    expect(sandboxTokens).toEqual([
      'allow-scripts',
      'allow-popups',
      'allow-popups-to-escape-sandbox',
      'allow-downloads',
    ]);
    expect(sandboxTokens).not.toContain('allow-same-origin');

    const postMessage = vi.spyOn(frame.contentWindow, 'postMessage').mockImplementation(() => {});
    fireEvent.load(frame);
    expect(postMessage).toHaveBeenCalledWith(
      { type: 'respro:report-theme', theme: 'dark' },
      '*',
    );
  });

  it('accepts opaque-origin ("null") messages only when event.source matches frame.contentWindow', () => {
    renderPreview();
    const frame = document.querySelector('iframe.workspace-frame');

    // Foreign source with opaque origin must be rejected.
    dispatchMessage({
      data: { type: 'respro:open-plot', src: 'blob:plot', alt: 'Spoofed' },
      origin: 'null',
      source: window,
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    // Missing source must be rejected.
    dispatchMessage({
      data: { type: 'respro:open-plot', src: 'blob:plot', alt: 'No source' },
      origin: 'null',
      source: null,
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    // Foreign origin even with frame source must be rejected.
    dispatchMessage({
      data: { type: 'respro:open-plot', src: 'blob:plot', alt: 'Wrong origin' },
      origin: 'https://evil.example',
      source: frame.contentWindow,
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    // Authentic frame source with opaque 'null' origin is accepted.
    dispatchMessage({
      data: { type: 'respro:open-plot', src: 'blob:plot', alt: 'Valid plot' },
      origin: 'null',
      source: frame.contentWindow,
    });
    const dialog = screen.getByRole('dialog', { name: /resistance plot/i });
    expect(dialog.querySelector('img')).toHaveAttribute('src', 'blob:plot');
  });

  it('allowlists safe asset srcs for plot and structure popouts and rejects dangerous schemes', () => {
    renderPreview();
    const frame = document.querySelector('iframe.workspace-frame');

    const unsafeSrcs = [
      'javascript:alert(1)',
      'vbscript:msgbox(1)',
      'data:text/html,<script>alert(1)</script>',
      'data:image/svg+xml,<svg onload=alert(1)>',
      'file:///etc/passwd',
      '//evil.example/plot.png',
      'https://evil.example/plot.png',
      '/api/jobs/123',
    ];
    for (const src of unsafeSrcs) {
      dispatchMessage({
        data: { type: 'respro:open-plot', src, alt: 'Bad' },
        origin: 'null',
        source: frame.contentWindow,
      });
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

      dispatchMessage({
        data: { type: 'respro:open-structure', src, title: 'Bad' },
        origin: 'null',
        source: frame.contentWindow,
      });
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    }

    // PubChem is allowed for structures, not plots.
    const pubchemUrl = 'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/135398513/PNG';
    dispatchMessage({
      data: { type: 'respro:open-plot', src: pubchemUrl, alt: 'Wrong type' },
      origin: 'null',
      source: frame.contentWindow,
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    dispatchMessage({
      data: { type: 'respro:open-structure', src: pubchemUrl, title: 'Acyclovir' },
      origin: 'null',
      source: frame.contentWindow,
    });
    const structDialog = screen.getByRole('dialog', { name: /chemical structure of acyclovir/i });
    expect(structDialog.querySelector('img')).toHaveAttribute('src', pubchemUrl);

    // Data SVG and /api/artifact are allowed for plots.
    const dataSvg = 'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=';
    dispatchMessage({
      data: { type: 'respro:open-plot', src: dataSvg, alt: 'SVG plot' },
      origin: 'null',
      source: frame.contentWindow,
    });
    expect(screen.getByRole('dialog', { name: /resistance plot/i }).querySelector('img')).toHaveAttribute(
      'src',
      dataSvg,
    );

    expect(isAllowedReportAssetSrc('/api/artifact?artifact_id=a1')).toBe(true);
    expect(
      isAllowedReportAssetSrc('http://127.0.0.1:8000/api/artifact?artifact_id=a1', {
        reportOrigin: 'http://127.0.0.1:8000',
      }),
    ).toBe(true);
  });

  it('rejects non-finite/non-positive report heights and clamps huge values', () => {
    renderPreview();
    const frame = document.querySelector('iframe.workspace-frame');

    for (const badHeight of [NaN, Infinity, -Infinity, 0, -50, '500', null]) {
      dispatchMessage({
        data: { type: 'respro:report-height', height: badHeight },
        origin: 'null',
        source: frame.contentWindow,
      });
      expect(frame.style.height).toBe('');
    }

    dispatchMessage({
      data: { type: 'respro:report-height', height: 420.2 },
      origin: 'null',
      source: frame.contentWindow,
    });
    expect(frame.style.height).toBe('423px');

    dispatchMessage({
      data: { type: 'respro:report-height', height: 1e9 },
      origin: 'null',
      source: frame.contentWindow,
    });
    expect(frame.style.height).toBe('100000px');
  });

  it('guards non-string postMessage fields with safe fallbacks so malformed payloads never crash React', () => {
    renderPreview();
    const frame = document.querySelector('iframe.workspace-frame');

    dispatchMessage({
      data: { type: 'respro:open-plot', src: '/api/artifact?id=1', alt: { bad: 1 } },
      origin: 'null',
      source: frame.contentWindow,
    });
    const plotImg = screen.getByRole('dialog', { name: /resistance plot/i }).querySelector('img');
    expect(plotImg).toHaveAttribute('alt', 'Resistance plot');

    dispatchMessage({
      data: {
        type: 'respro:open-structure',
        src: 'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/1/PNG',
        title: ['bad'],
      },
      origin: 'null',
      source: frame.contentWindow,
    });
    const structDialog = screen.getByRole('dialog', { name: /chemical structure of drug structure/i });
    expect(structDialog.querySelector('.report-preview-structure-title')).toHaveTextContent('Drug structure');

    dispatchMessage({
      data: {
        type: 'respro:open-sequence',
        title: { bad: 1 },
        ntSequence: { bad: 2 },
        aaSequence: ['M', 'A'],
      },
      origin: 'null',
      source: frame.contentWindow,
    });
    const seqDialog = screen.getByRole('dialog', { name: /feature sequence/i });
    expect(seqDialog.querySelector('.report-preview-sequence-title')).toHaveTextContent('Feature sequence');
    expect(seqDialog.querySelector('.report-preview-sequence-block')).toHaveTextContent('');
    expect(seqDialog.querySelector('.seq-toggle-btn:first-child')).toBeDisabled();
    expect(seqDialog.querySelector('.seq-toggle-btn:last-child')).toBeDisabled();
  });

  it('restricts blob URLs to opaque/same-origin/report-origin and rejects HTTP/HTTPS userinfo', () => {
    expect(isAllowedReportAssetSrc('blob:null/550e8400-e29b-41d4-a716-446655440000')).toBe(true);
    expect(isAllowedReportAssetSrc(`blob:${window.location.origin}/550e8400-e29b-41d4-a716-446655440000`)).toBe(true);
    expect(
      isAllowedReportAssetSrc('blob:http://127.0.0.1:8000/550e8400-e29b-41d4-a716-446655440000', {
        reportOrigin: 'http://127.0.0.1:8000',
      }),
    ).toBe(true);
    expect(isAllowedReportAssetSrc('blob:plot')).toBe(true);

    expect(isAllowedReportAssetSrc('blob:https://evil.example/abc')).toBe(false);
    expect(isAllowedReportAssetSrc('blob:http://evil.example/abc')).toBe(false);
    expect(
      isAllowedReportAssetSrc(`blob:${window.location.origin.replace('://', '://user:pass@')}/abc`),
    ).toBe(false);
    expect(
      isAllowedReportAssetSrc('blob:http://user:pass@127.0.0.1:8000/abc', {
        reportOrigin: 'http://127.0.0.1:8000',
      }),
    ).toBe(false);
    expect(isAllowedReportAssetSrc('blob:javascript:alert(1)')).toBe(false);
    expect(isAllowedReportAssetSrc('blob:data:text/html,foo')).toBe(false);
    expect(isAllowedReportAssetSrc('blob:vbscript:msgbox(1)')).toBe(false);
    expect(isAllowedReportAssetSrc('blob://evil.example/abc')).toBe(false);
    expect(isAllowedReportAssetSrc('blob:null//evil.example/abc')).toBe(false);

    expect(
      isAllowedReportAssetSrc('https://user:pass@pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/1/PNG', {
        allowPubchem: true,
      }),
    ).toBe(false);
    expect(
      isAllowedReportAssetSrc(`${window.location.origin.replace('://', '://user:pass@')}/api/artifact?id=1`),
    ).toBe(false);
    expect(
      isAllowedReportAssetSrc('http://user:pass@127.0.0.1:8000/api/artifact?id=1', {
        reportOrigin: 'http://127.0.0.1:8000',
      }),
    ).toBe(false);
  });

  it('clears plot, structure, sequence, sequenceView, and frame height when inlineReportPath changes', () => {
    const { rerender } = renderPreview({ inlineReportPath: 'rep-1', showFrame: true });
    const frame = document.querySelector('iframe.workspace-frame');

    dispatchMessage({
      data: { type: 'respro:report-height', height: 500 },
      origin: 'null',
      source: frame.contentWindow,
    });
    expect(frame.style.height).toBe('502px');

    dispatchMessage({
      data: { type: 'respro:open-plot', src: 'blob:plot', alt: 'Plot 1' },
      origin: 'null',
      source: frame.contentWindow,
    });
    dispatchMessage({
      data: {
        type: 'respro:open-structure',
        src: 'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/1/PNG',
        title: 'Drug 1',
      },
      origin: 'null',
      source: frame.contentWindow,
    });
    dispatchMessage({
      data: {
        type: 'respro:open-sequence',
        title: 'Gene 1',
        ntSequence: 'ACGT',
        aaSequence: 'MV',
      },
      origin: 'null',
      source: frame.contentWindow,
    });

    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    const seqDialog = screen.getByRole('dialog', { name: /feature sequence/i });
    fireEvent.click(seqDialog.querySelector('.seq-toggle-btn:last-child'));
    expect(seqDialog.querySelector('.report-preview-sequence-block')).toHaveTextContent('MV');

    rerender(
      <ThemeContext.Provider value="light">
        <ReportPreviewSection
          showFrame
          inlineReportPath="rep-2"
          buildReportUrl={(id) => `/api/report?artifact_id=${id}`}
        />
      </ThemeContext.Provider>,
    );

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.querySelector('iframe.workspace-frame').style.height).toBe('');

    // Re-opening sequence with both ntSequence and aaSequence resets back to DNA ('nt') view.
    const nextFrame = document.querySelector('iframe.workspace-frame');
    dispatchMessage({
      data: {
        type: 'respro:open-sequence',
        title: 'Gene 2',
        ntSequence: 'GGGG',
        aaSequence: 'GG',
      },
      origin: 'null',
      source: nextFrame.contentWindow,
    });
    expect(screen.getByRole('dialog', { name: /feature sequence/i }).querySelector('.report-preview-sequence-block'))
      .toHaveTextContent('GGGG');

    // Clearing inlineReportPath to '' (with showFrame=false, matching AnalyzePage) keeps dialogs closed.
    rerender(
      <ThemeContext.Provider value="light">
        <ReportPreviewSection
          showFrame={false}
          inlineReportPath=""
          buildReportUrl={(id) => `/api/report?artifact_id=${id}`}
        />
      </ThemeContext.Provider>,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('clears hosted modals and frame height when showFrame toggles to false', () => {
    const { rerender } = renderPreview({ inlineReportPath: 'rep-1', showFrame: true });
    const frame = document.querySelector('iframe.workspace-frame');

    dispatchMessage({
      data: { type: 'respro:report-height', height: 600 },
      origin: 'null',
      source: frame.contentWindow,
    });
    dispatchMessage({
      data: {
        type: 'respro:open-structure',
        src: 'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/1/PNG',
        title: 'Drug 1',
      },
      origin: 'null',
      source: frame.contentWindow,
    });
    dispatchMessage({
      data: {
        type: 'respro:open-sequence',
        title: 'Gene 1',
        ntSequence: 'ACGT',
        aaSequence: 'MV',
      },
      origin: 'null',
      source: frame.contentWindow,
    });
    expect(screen.getAllByRole('dialog')).toHaveLength(1);

    rerender(
      <ThemeContext.Provider value="light">
        <ReportPreviewSection
          showFrame={false}
          inlineReportPath="rep-1"
          buildReportUrl={(id) => `/api/report?artifact_id=${id}`}
        />
      </ThemeContext.Provider>,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    rerender(
      <ThemeContext.Provider value="light">
        <ReportPreviewSection
          showFrame
          inlineReportPath="rep-1"
          buildReportUrl={(id) => `/api/report?artifact_id=${id}`}
        />
      </ThemeContext.Provider>,
    );
    expect(document.querySelector('iframe.workspace-frame').style.height).toBe('');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('ReportPreviewSection accessibility (WA-053)', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  function renderWithMain(ui) {
    return render(
      <ThemeContext.Provider value="light">
        <main id="main-content" tabIndex={-1}>
          <button type="button">Parent action</button>
          {ui}
        </main>
      </ThemeContext.Provider>,
    );
  }

  it('synchronously focuses the close button (not autoFocus) on open for plot, structure, and sequence modals', () => {
    renderWithMain(
      <ReportPreviewSection
        showFrame
        inlineReportPath="rep-1"
        buildReportUrl={(id) => `/api/report?artifact_id=${id}`}
      />,
    );
    const frame = document.querySelector('iframe.workspace-frame');

    dispatchMessage({
      data: { type: 'respro:open-plot', src: 'blob:plot', alt: 'Plot' },
      origin: 'null',
      source: frame.contentWindow,
    });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close resistance plot' }));
    fireEvent.click(screen.getByRole('button', { name: 'Close resistance plot' }));

    dispatchMessage({
      data: {
        type: 'respro:open-structure',
        src: 'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/1/PNG',
        title: 'Acyclovir',
      },
      origin: 'null',
      source: frame.contentWindow,
    });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close chemical structure' }));
    fireEvent.click(screen.getByRole('button', { name: 'Close chemical structure' }));

    dispatchMessage({
      data: {
        type: 'respro:open-sequence',
        title: 'UL23',
        ntSequence: 'ACGT',
        aaSequence: 'MV',
      },
      origin: 'null',
      source: frame.contentWindow,
    });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close feature sequence' }));
  });

  it('traps Tab and Shift+Tab inside single-control and multi-control modals and pulls escaped focus back', () => {
    renderWithMain(
      <ReportPreviewSection
        showFrame
        inlineReportPath="rep-1"
        buildReportUrl={(id) => `/api/report?artifact_id=${id}`}
      />,
    );
    const frame = document.querySelector('iframe.workspace-frame');

    // Single-control modal (plot)
    dispatchMessage({
      data: { type: 'respro:open-plot', src: 'blob:plot', alt: 'Plot' },
      origin: 'null',
      source: frame.contentWindow,
    });
    const plotClose = screen.getByRole('button', { name: 'Close resistance plot' });
    expect(document.activeElement).toBe(plotClose);

    const tabSingle = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    act(() => {
      window.dispatchEvent(tabSingle);
    });
    expect(tabSingle.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(plotClose);

    const shiftTabSingle = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true });
    act(() => {
      window.dispatchEvent(shiftTabSingle);
    });
    expect(shiftTabSingle.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(plotClose);

    // Escaped focus on document.body is pulled back inside modal
    plotClose.blur();
    expect(document.activeElement).toBe(document.body);
    const pullBack = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    act(() => {
      window.dispatchEvent(pullBack);
    });
    expect(pullBack.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(plotClose);

    fireEvent.click(plotClose);

    // Multi-control modal (sequence with DNA & Protein)
    dispatchMessage({
      data: {
        type: 'respro:open-sequence',
        title: 'UL23',
        ntSequence: 'ACGT',
        aaSequence: 'MV',
      },
      origin: 'null',
      source: frame.contentWindow,
    });
    const seqClose = screen.getByRole('button', { name: 'Close feature sequence' });
    const dnaBtn = screen.getByRole('button', { name: 'DNA' });
    const proteinBtn = screen.getByRole('button', { name: 'Protein' });

    // Shift+Tab from first wraps to last (Protein)
    const wrapBackward = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true });
    act(() => {
      window.dispatchEvent(wrapBackward);
    });
    expect(wrapBackward.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(proteinBtn);

    // Tab from last wraps to first (close)
    const wrapForward = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    act(() => {
      window.dispatchEvent(wrapForward);
    });
    expect(wrapForward.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(seqClose);

    // Tab on middle element (DNA) is NOT prevented
    dnaBtn.focus();
    const middleTab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    act(() => {
      window.dispatchEvent(middleTab);
    });
    expect(middleTab.defaultPrevented).toBe(false);

    // Modified Tab / defaultPrevented / isComposing ignored
    proteinBtn.focus();
    const ctrlTab = new KeyboardEvent('keydown', { key: 'Tab', ctrlKey: true, bubbles: true, cancelable: true });
    act(() => {
      window.dispatchEvent(ctrlTab);
    });
    expect(ctrlTab.defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(proteinBtn);

    const composingTab = new KeyboardEvent('keydown', { key: 'Tab', isComposing: true, bubbles: true, cancelable: true });
    act(() => {
      window.dispatchEvent(composingTab);
    });
    expect(composingTab.defaultPrevented).toBe(false);

    // Sequence with disabled Protein button skips Protein on wrap
    fireEvent.click(seqClose);
    dispatchMessage({
      data: {
        type: 'respro:open-sequence',
        title: 'UL23',
        ntSequence: 'ACGT',
        aaSequence: '',
      },
      origin: 'null',
      source: frame.contentWindow,
    });
    const seqCloseOnlyDna = screen.getByRole('button', { name: 'Close feature sequence' });
    const dnaEnabled = screen.getByRole('button', { name: 'DNA' });
    expect(screen.getByRole('button', { name: 'Protein' })).toBeDisabled();

    const shiftWrap = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true });
    act(() => {
      window.dispatchEvent(shiftWrap);
    });
    expect(document.activeElement).toBe(dnaEnabled);

    const forwardWrap = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    act(() => {
      window.dispatchEvent(forwardWrap);
    });
    expect(document.activeElement).toBe(seqCloseOnlyDna);
  });

  it('closes on unmodified Escape with preventDefault and ignores modified, composing, or defaultPrevented Escape', () => {
    renderWithMain(
      <ReportPreviewSection
        showFrame
        inlineReportPath="rep-1"
        buildReportUrl={(id) => `/api/report?artifact_id=${id}`}
      />,
    );
    const frame = document.querySelector('iframe.workspace-frame');

    dispatchMessage({
      data: { type: 'respro:open-plot', src: 'blob:plot', alt: 'Plot' },
      origin: 'null',
      source: frame.contentWindow,
    });
    expect(screen.getByRole('dialog', { name: 'Resistance plot' })).toBeInTheDocument();

    for (const mod of [
      { shiftKey: true },
      { ctrlKey: true },
      { altKey: true },
      { metaKey: true },
      { isComposing: true },
    ]) {
      const ev = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true, ...mod });
      act(() => {
        window.dispatchEvent(ev);
      });
      expect(ev.defaultPrevented).toBe(false);
      expect(screen.getByRole('dialog', { name: 'Resistance plot' })).toBeInTheDocument();
    }

    const preventedEsc = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    preventedEsc.preventDefault();
    act(() => {
      window.dispatchEvent(preventedEsc);
    });
    expect(screen.getByRole('dialog', { name: 'Resistance plot' })).toBeInTheDocument();

    // Document-dispatched Escape bubbles to window and closes + prevents default
    const docEsc = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    act(() => {
      document.dispatchEvent(docEsc);
    });
    expect(docEsc.defaultPrevented).toBe(true);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('restores focus to previous parent trigger on close/backdrop/Escape/reset and falls back to #main-content when trigger is iframe or detached', () => {
    const { rerender } = renderWithMain(
      <ReportPreviewSection
        showFrame
        inlineReportPath="rep-1"
        buildReportUrl={(id) => `/api/report?artifact_id=${id}`}
      />,
    );
    const frame = document.querySelector('iframe.workspace-frame');
    const parentBtn = screen.getByRole('button', { name: 'Parent action' });
    const mainEl = document.getElementById('main-content');

    // 1. Trigger is parent button -> close button restores focus
    parentBtn.focus();
    dispatchMessage({
      data: { type: 'respro:open-plot', src: 'blob:plot', alt: 'Plot' },
      origin: 'null',
      source: frame.contentWindow,
    });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close resistance plot' }));
    fireEvent.click(screen.getByRole('button', { name: 'Close resistance plot' }));
    expect(document.activeElement).toBe(parentBtn);

    // 2. Backdrop click restores focus
    parentBtn.focus();
    dispatchMessage({
      data: { type: 'respro:open-plot', src: 'blob:plot', alt: 'Plot' },
      origin: 'null',
      source: frame.contentWindow,
    });
    fireEvent.click(document.querySelector('.report-preview-plot-backdrop'));
    expect(document.activeElement).toBe(parentBtn);

    // 3. Switching active modal: opening sequence replaces structure cleanly, and closing restores parentBtn
    parentBtn.focus();
    dispatchMessage({
      data: {
        type: 'respro:open-structure',
        src: 'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/1/PNG',
        title: 'Acyclovir',
      },
      origin: 'null',
      source: frame.contentWindow,
    });
    dispatchMessage({
      data: {
        type: 'respro:open-sequence',
        title: 'UL23',
        ntSequence: 'ACGT',
        aaSequence: 'MV',
      },
      origin: 'null',
      source: frame.contentWindow,
    });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close feature sequence' }));
    fireEvent.click(screen.getByRole('button', { name: 'Close feature sequence' }));
    expect(document.activeElement).toBe(parentBtn);

    // 4. Trigger is iframe -> falls back to #main-content without scrolling viewport
    const mainFocusSpy = vi.spyOn(mainEl, 'focus');
    frame.focus();
    dispatchMessage({
      data: { type: 'respro:open-plot', src: 'blob:plot', alt: 'Plot' },
      origin: 'null',
      source: frame.contentWindow,
    });
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });
    expect(document.activeElement).toBe(mainEl);
    expect(mainFocusSpy).toHaveBeenCalledWith({ preventScroll: true });
    mainFocusSpy.mockRestore();

    // 5. inlineReportPath reset while modal open -> restores focus to detached fallback (#main-content)
    const tempTrigger = document.createElement('button');
    tempTrigger.type = 'button';
    mainEl.appendChild(tempTrigger);
    tempTrigger.focus();
    dispatchMessage({
      data: { type: 'respro:open-plot', src: 'blob:plot', alt: 'Plot' },
      origin: 'null',
      source: frame.contentWindow,
    });
    tempTrigger.remove();
    rerender(
      <ThemeContext.Provider value="light">
        <main id="main-content" tabIndex={-1}>
          <button type="button">Parent action</button>
          <ReportPreviewSection
            showFrame
            inlineReportPath="rep-2"
            buildReportUrl={(id) => `/api/report?artifact_id=${id}`}
          />
        </main>
      </ThemeContext.Provider>,
    );
    expect(document.activeElement).toBe(mainEl);
  });

  it('no-ops cleanly without #main-content and exposes role="group" + aria-pressed on Sequence view toggle', () => {
    const { unmount } = renderPreview();
    const frame = document.querySelector('iframe.workspace-frame');

    dispatchMessage({
      data: {
        type: 'respro:open-sequence',
        title: 'UL23',
        ntSequence: 'ACGT',
        aaSequence: 'MV',
      },
      origin: 'null',
      source: frame.contentWindow,
    });

    const toggleGroup = screen.getByRole('group', { name: 'Sequence view' });
    const dnaBtn = within(toggleGroup).getByRole('button', { name: 'DNA' });
    const proteinBtn = within(toggleGroup).getByRole('button', { name: 'Protein' });
    expect(dnaBtn).toHaveAttribute('aria-pressed', 'true');
    expect(proteinBtn).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(proteinBtn);
    expect(dnaBtn).toHaveAttribute('aria-pressed', 'false');
    expect(proteinBtn).toHaveAttribute('aria-pressed', 'true');

    // Missing #main-content fallback -> closing does not throw
    fireEvent.click(screen.getByRole('button', { name: 'Close feature sequence' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    // Unmounting while open does not throw
    dispatchMessage({
      data: { type: 'respro:open-plot', src: 'blob:plot', alt: 'Plot' },
      origin: 'null',
      source: frame.contentWindow,
    });
    expect(() => unmount()).not.toThrow();
  });
});
