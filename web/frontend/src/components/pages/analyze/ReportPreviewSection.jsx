import { memo, useContext, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { ThemeContext } from '../../../hooks/useTheme';

const MAX_REPORT_FRAME_HEIGHT = 100000;
const PUBCHEM_ORIGIN = 'https://pubchem.ncbi.nlm.nih.gov';
const SAFE_DATA_IMAGE_RE = /^data:image\/(?:png|jpeg|jpg|gif|webp|svg\+xml)(?:;charset=[^;,\s]+)?;base64,[A-Za-z0-9+/=]+$/i;
const SAFE_BLOB_FIXTURE_RE = /^blob:[A-Za-z0-9._-]+$/i;
const SAFE_OPAQUE_BLOB_RE = /^blob:null\/[^/\\\s][^\s\\]*$/i;
const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

function isElementVisible(el, container) {
  if (!el || el.disabled || el.hasAttribute('disabled')) return false;
  if (el.closest('[hidden], [aria-hidden="true"], [inert], fieldset[disabled]')) return false;
  if (typeof window?.getComputedStyle !== 'function') return true;
  let cur = el;
  while (cur && cur.nodeType === 1) {
    const style = window.getComputedStyle(cur);
    if (style.display === 'none' || style.visibility === 'hidden') {
      return false;
    }
    if (cur === container) break;
    cur = cur.parentElement;
  }
  return true;
}

function getFocusableElements(container) {
  if (!container) return [];
  return Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR)).filter(
    (el) => isElementVisible(el, container),
  );
}

function isRestorableTrigger(el, frameEl) {
  if (!el || !el.isConnected || typeof el.focus !== 'function') return false;
  if (el === document.body || el === document.documentElement) return false;
  if (el === frameEl || el.tagName === 'IFRAME') return false;
  return isElementVisible(el, document.documentElement);
}

function restorePreviousFocus(previousEl, frameEl) {
  if (isRestorableTrigger(previousEl, frameEl)) {
    previousEl.focus();
    if (document.activeElement === previousEl) return;
  }
  const mainFallback = document.querySelector('main[tabindex], #main-content[tabindex]');
  if (mainFallback && typeof mainFallback.focus === 'function') {
    mainFallback.focus();
  }
}

function stringOrFallback(value, fallback) {
  return typeof value === 'string' && value ? value : fallback;
}

export function isAllowedReportAssetSrc(src, { allowPubchem = false, reportOrigin = '' } = {}) {
  if (typeof src !== 'string') return false;
  const trimmed = src.trim();
  if (!trimmed || /[\u0000-\u001F\u007F]/.test(trimmed) || trimmed.startsWith('//')) {
    return false;
  }
  if (/^blob:/i.test(trimmed)) {
    if (/\s|\\/.test(trimmed)) {
      return false;
    }
    if (SAFE_BLOB_FIXTURE_RE.test(trimmed) || SAFE_OPAQUE_BLOB_RE.test(trimmed)) {
      return true;
    }
    const innerTarget = trimmed.slice(5);
    if (!/^https?:\/\//i.test(innerTarget)) {
      return false;
    }
    try {
      const inner = new URL(innerTarget);
      if (
        (inner.protocol !== 'http:' && inner.protocol !== 'https:')
        || inner.username
        || inner.password
      ) {
        return false;
      }
      return (
        inner.origin === window.location.origin
        || Boolean(reportOrigin && inner.origin === reportOrigin)
      );
    } catch {
      return false;
    }
  }
  if (/^data:/i.test(trimmed)) {
    return SAFE_DATA_IMAGE_RE.test(trimmed);
  }
  try {
    const parsed = new URL(trimmed, window.location.href);
    if (
      (parsed.protocol !== 'http:' && parsed.protocol !== 'https:')
      || parsed.username
      || parsed.password
    ) {
      return false;
    }
    if (allowPubchem && parsed.origin === PUBCHEM_ORIGIN) {
      return true;
    }
    const isAllowedOrigin =
      parsed.origin === window.location.origin
      || Boolean(reportOrigin && parsed.origin === reportOrigin);
    return isAllowedOrigin && parsed.pathname === '/api/artifact';
  } catch {
    return false;
  }
}

function ReportPreviewModal({
  ariaLabel,
  closeLabel,
  panelClassName = '',
  panelRef,
  closeButtonRef,
  onClose,
  children,
}) {
  return (
    <div className="report-preview-plot-modal" role="dialog" aria-modal="true" aria-label={ariaLabel}>
      <div className="report-preview-plot-backdrop" onClick={onClose} aria-hidden="true" />
      <section
        ref={panelRef}
        tabIndex={-1}
        className={`report-preview-plot-panel${panelClassName ? ` ${panelClassName}` : ''}`}
      >
        <button
          ref={closeButtonRef}
          type="button"
          className="report-preview-plot-close"
          aria-label={closeLabel}
          onClick={onClose}
        >
          &times;
        </button>
        {children}
      </section>
    </div>
  );
}

export const ReportPreviewSection = memo(function ReportPreviewSection({ showFrame, inlineReportPath, buildReportUrl }) {
  const resolvedTheme = useContext(ThemeContext);
  const reportFrameRef = useRef(null);
  const plotPanelRef = useRef(null);
  const plotCloseRef = useRef(null);
  const structurePanelRef = useRef(null);
  const structureCloseRef = useRef(null);
  const sequencePanelRef = useRef(null);
  const sequenceCloseRef = useRef(null);
  const previousFocusRef = useRef(null);
  const wasOpenRef = useRef(false);
  const prevModalStateRef = useRef({ plot: false, structure: false, sequence: false });
  const [reportFrameHeight, setReportFrameHeight] = useState(null);
  const [hostedPlot, setHostedPlot] = useState(null);
  const [hostedStructure, setHostedStructure] = useState(null);
  const [hostedSequence, setHostedSequence] = useState(null);
  const [sequenceView, setSequenceView] = useState('nt');

  const reportUrl = inlineReportPath ? buildReportUrl(inlineReportPath) : '';
  // Resolve against window.location.href so same-origin relative report URLs
  // still yield a valid origin on remount before the iframe finishes loading.
  const reportOrigin = (() => {
    if (!reportUrl) return '';
    try {
      return new URL(reportUrl, window.location.href).origin;
    } catch {
      return '';
    }
  })();

  useEffect(() => {
    const frame = reportFrameRef.current;
    if (!frame || !resolvedTheme) return;
    try {
      frame.contentWindow?.postMessage(
        { type: 'respro:report-theme', theme: resolvedTheme },
        '*',
      );
    } catch {
      // The iframe load handler retries once the report listener is ready.
    }
  }, [resolvedTheme, inlineReportPath, reportUrl]);

  useEffect(() => {
    setReportFrameHeight(null);
    setHostedPlot(null);
    setHostedStructure(null);
    setHostedSequence(null);
    setSequenceView('nt');
  }, [inlineReportPath, showFrame]);

  useEffect(() => {
    const handleMessage = (event) => {
      const frameWindow = reportFrameRef.current?.contentWindow;
      if (!frameWindow || event.source !== frameWindow) return;
      if (event.origin !== 'null' && event.origin !== reportOrigin) return;

      if (!event.data || typeof event.data !== 'object') return;
      const { type } = event.data;
      if (type === 'respro:open-plot') {
        if (isAllowedReportAssetSrc(event.data.src, { reportOrigin })) {
          setHostedPlot({
            src: event.data.src.trim(),
            alt: stringOrFallback(event.data.alt, 'Resistance plot'),
          });
        }
      } else if (type === 'respro:open-structure') {
        if (isAllowedReportAssetSrc(event.data.src, { allowPubchem: true, reportOrigin })) {
          setHostedStructure({
            src: event.data.src.trim(),
            title: stringOrFallback(event.data.title, 'Drug structure'),
          });
        }
      } else if (type === 'respro:open-sequence') {
        const ntSequence = typeof event.data.ntSequence === 'string' ? event.data.ntSequence : '';
        const aaSequence = typeof event.data.aaSequence === 'string' ? event.data.aaSequence : '';
        setHostedSequence({
          title: stringOrFallback(event.data.title, 'Feature sequence'),
          ntSequence,
          aaSequence,
        });
        setSequenceView(ntSequence ? 'nt' : 'aa');
      } else if (
        type === 'respro:report-height'
        && typeof event.data.height === 'number'
        && Number.isFinite(event.data.height)
        && event.data.height > 0
      ) {
        setReportFrameHeight(Math.min(Math.ceil(event.data.height) + 2, MAX_REPORT_FRAME_HEIGHT));
      }
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [reportOrigin]);

  const isPlotOpen = Boolean(hostedPlot);
  const isStructureOpen = Boolean(hostedStructure);
  const isSequenceOpen = Boolean(hostedSequence);
  const hasOpenModal = isPlotOpen || isStructureOpen || isSequenceOpen;

  useLayoutEffect(() => {
    const prevState = prevModalStateRef.current;
    const newlyOpenedPlot = isPlotOpen && !prevState.plot;
    const newlyOpenedStructure = isStructureOpen && !prevState.structure;
    const newlyOpenedSequence = isSequenceOpen && !prevState.sequence;

    if (hasOpenModal && !wasOpenRef.current) {
      const activeEl = document.activeElement;
      const startedInside = Boolean(
        activeEl && (
          plotPanelRef.current?.contains(activeEl)
          || structurePanelRef.current?.contains(activeEl)
          || sequencePanelRef.current?.contains(activeEl)
        ),
      );
      previousFocusRef.current =
        isRestorableTrigger(activeEl, reportFrameRef.current) && !startedInside
          ? activeEl
          : null;
      wasOpenRef.current = true;
    }

    if (hasOpenModal) {
      const focusModal = (closeRef, panelRef) => {
        if (closeRef.current && isElementVisible(closeRef.current, panelRef.current)) {
          closeRef.current.focus();
        } else {
          panelRef.current?.focus();
        }
      };
      if (newlyOpenedSequence) {
        focusModal(sequenceCloseRef, sequencePanelRef);
      } else if (newlyOpenedStructure) {
        focusModal(structureCloseRef, structurePanelRef);
      } else if (newlyOpenedPlot) {
        focusModal(plotCloseRef, plotPanelRef);
      } else {
        const activeEl = document.activeElement;
        const inOpenPanel = Boolean(
          activeEl && (
            (isSequenceOpen && sequencePanelRef.current?.contains(activeEl))
            || (isStructureOpen && structurePanelRef.current?.contains(activeEl))
            || (isPlotOpen && plotPanelRef.current?.contains(activeEl))
          ),
        );
        if (!inOpenPanel) {
          if (isSequenceOpen) focusModal(sequenceCloseRef, sequencePanelRef);
          else if (isStructureOpen) focusModal(structureCloseRef, structurePanelRef);
          else if (isPlotOpen) focusModal(plotCloseRef, plotPanelRef);
        }
      }
    } else if (wasOpenRef.current) {
      const prev = previousFocusRef.current;
      previousFocusRef.current = null;
      wasOpenRef.current = false;
      restorePreviousFocus(prev, reportFrameRef.current);
    }

    prevModalStateRef.current = {
      plot: isPlotOpen,
      structure: isStructureOpen,
      sequence: isSequenceOpen,
    };
  }, [hasOpenModal, isPlotOpen, isStructureOpen, isSequenceOpen]);

  useEffect(() => {
    if (!hasOpenModal) return undefined;
    const handleKeyDown = (event) => {
      if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
      if (event.key === 'Escape') {
        if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
        event.preventDefault();
        setHostedPlot(null);
        setHostedStructure(null);
        setHostedSequence(null);
        return;
      }
      if (event.key === 'Tab') {
        if (event.altKey || event.ctrlKey || event.metaKey) return;
        const active = document.activeElement;
        const openPanels = [
          isSequenceOpen ? sequencePanelRef.current : null,
          isStructureOpen ? structurePanelRef.current : null,
          isPlotOpen ? plotPanelRef.current : null,
        ].filter(Boolean);
        const container = openPanels.find((panel) => active && panel.contains(active)) || openPanels[0];
        if (!container) return;

        const focusable = getFocusableElements(container);
        if (focusable.length === 0) {
          event.preventDefault();
          container.focus();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (!container.contains(active) || active === container) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
          return;
        }
        if (event.shiftKey && active === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && active === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [hasOpenModal, isPlotOpen, isStructureOpen, isSequenceOpen]);

  return (
    <>
      {showFrame ? (
        <article className="full-width-tile tab-primary-tile">
          <iframe
            ref={reportFrameRef}
            title="ResistanceProfiler report"
            src={reportUrl}
            className="workspace-frame"
            sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox allow-downloads"
            style={reportFrameHeight ? { height: `${reportFrameHeight}px` } : undefined}
            onLoad={(event) => {
              event.currentTarget.contentWindow?.postMessage(
                { type: 'respro:report-theme', theme: resolvedTheme },
                '*',
              );
            }}
          />
        </article>
      ) : null}

      {hostedPlot ? (
        <ReportPreviewModal
          ariaLabel="Resistance plot"
          closeLabel="Close resistance plot"
          panelRef={plotPanelRef}
          closeButtonRef={plotCloseRef}
          onClose={() => setHostedPlot(null)}
        >
          <img src={hostedPlot.src} alt={hostedPlot.alt} className="report-preview-plot-image" />
        </ReportPreviewModal>
      ) : null}

      {hostedStructure ? (
        <ReportPreviewModal
          ariaLabel={`Chemical structure of ${hostedStructure.title}`}
          closeLabel="Close chemical structure"
          panelClassName="report-preview-structure-panel"
          panelRef={structurePanelRef}
          closeButtonRef={structureCloseRef}
          onClose={() => setHostedStructure(null)}
        >
          <h2 className="report-preview-structure-title">{hostedStructure.title}</h2>
          <img
            src={hostedStructure.src}
            alt={`Chemical structure of ${hostedStructure.title}`}
            className="report-preview-plot-image"
          />
        </ReportPreviewModal>
      ) : null}

      {hostedSequence ? (
        <ReportPreviewModal
          ariaLabel="Feature sequence"
          closeLabel="Close feature sequence"
          panelClassName="report-preview-sequence-panel"
          panelRef={sequencePanelRef}
          closeButtonRef={sequenceCloseRef}
          onClose={() => setHostedSequence(null)}
        >
          <div className="report-preview-sequence-header">
            <h2 className="report-preview-sequence-title">{hostedSequence.title}</h2>
            <div className="report-preview-sequence-toggle" role="group" aria-label="Sequence view">
              <button
                type="button"
                className={`seq-toggle-btn${sequenceView === 'nt' ? ' seq-toggle-btn--active' : ''}`}
                aria-pressed={sequenceView === 'nt'}
                disabled={!hostedSequence.ntSequence}
                onClick={() => setSequenceView('nt')}
              >
                DNA
              </button>
              <button
                type="button"
                className={`seq-toggle-btn${sequenceView === 'aa' ? ' seq-toggle-btn--active' : ''}`}
                aria-pressed={sequenceView === 'aa'}
                disabled={!hostedSequence.aaSequence}
                onClick={() => setSequenceView('aa')}
              >
                Protein
              </button>
            </div>
          </div>
          <pre className="report-preview-sequence-block">
            {sequenceView === 'nt' ? hostedSequence.ntSequence : hostedSequence.aaSequence}
          </pre>
        </ReportPreviewModal>
      ) : null}
    </>
  );
});
