import { memo, useContext, useEffect, useLayoutEffect, useRef, useState } from 'react';

import {
  handleFocusTrapTab,
  isElementVisible,
  isRestorableTrigger,
  restorePreviousFocus,
} from '../../../hooks/useDialogFocusTrap';
import { ThemeContext } from '../../../hooks/useTheme';

const MAX_REPORT_FRAME_HEIGHT = 100000;
const PUBCHEM_ORIGIN = 'https://pubchem.ncbi.nlm.nih.gov';
const SAFE_DATA_IMAGE_RE = /^data:image\/(?:png|jpeg|jpg|gif|webp|svg\+xml)(?:;charset=[^;,\s]+)?;base64,[A-Za-z0-9+/=]+$/i;
const SAFE_BLOB_FIXTURE_RE = /^blob:[A-Za-z0-9._-]+$/i;
const SAFE_OPAQUE_BLOB_RE = /^blob:null\/[^/\\\s][^\s\\]*$/i;

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
    if (/\s|\\/.test(trimmed)) return false;
    if (SAFE_BLOB_FIXTURE_RE.test(trimmed) || SAFE_OPAQUE_BLOB_RE.test(trimmed)) return true;
    const innerTarget = trimmed.slice(5);
    if (!/^https?:\/\//i.test(innerTarget)) return false;
    try {
      const inner = new URL(innerTarget);
      if ((inner.protocol !== 'http:' && inner.protocol !== 'https:') || inner.username || inner.password) {
        return false;
      }
      return inner.origin === window.location.origin || Boolean(reportOrigin && inner.origin === reportOrigin);
    } catch {
      return false;
    }
  }
  if (/^data:/i.test(trimmed)) {
    return SAFE_DATA_IMAGE_RE.test(trimmed);
  }
  try {
    const parsed = new URL(trimmed, window.location.href);
    if ((parsed.protocol !== 'http:' && parsed.protocol !== 'https:') || parsed.username || parsed.password) {
      return false;
    }
    if (allowPubchem && parsed.origin === PUBCHEM_ORIGIN) return true;
    const isAllowedOrigin =
      parsed.origin === window.location.origin || Boolean(reportOrigin && parsed.origin === reportOrigin);
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
  const panelRef = useRef(null);
  const closeButtonRef = useRef(null);
  const previousFocusRef = useRef(null);
  const wasOpenRef = useRef(false);
  const [reportFrameHeight, setReportFrameHeight] = useState(null);
  const [activeModal, setActiveModal] = useState(null);
  const [sequenceView, setSequenceView] = useState('nt');

  const reportUrl = inlineReportPath ? buildReportUrl(inlineReportPath) : '';
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
      frame.contentWindow?.postMessage({ type: 'respro:report-theme', theme: resolvedTheme }, '*');
    } catch {
      // The iframe load handler retries once the report listener is ready.
    }
  }, [resolvedTheme, inlineReportPath, reportUrl]);

  useEffect(() => {
    setReportFrameHeight(null);
    setActiveModal(null);
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
          setActiveModal({
            kind: 'plot',
            src: event.data.src.trim(),
            alt: stringOrFallback(event.data.alt, 'Resistance plot'),
          });
        }
      } else if (type === 'respro:open-structure') {
        if (isAllowedReportAssetSrc(event.data.src, { allowPubchem: true, reportOrigin })) {
          setActiveModal({
            kind: 'structure',
            src: event.data.src.trim(),
            title: stringOrFallback(event.data.title, 'Drug structure'),
          });
        }
      } else if (type === 'respro:open-sequence') {
        const ntSequence = typeof event.data.ntSequence === 'string' ? event.data.ntSequence : '';
        const aaSequence = typeof event.data.aaSequence === 'string' ? event.data.aaSequence : '';
        setActiveModal({
          kind: 'sequence',
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

  const hasOpenModal = Boolean(activeModal);
  const modalKind = activeModal?.kind ?? null;

  useLayoutEffect(() => {
    if (hasOpenModal && !wasOpenRef.current) {
      const activeEl = document.activeElement;
      const startedInside = Boolean(activeEl && panelRef.current?.contains(activeEl));
      previousFocusRef.current =
        isRestorableTrigger(activeEl, reportFrameRef.current) && !startedInside ? activeEl : null;
      wasOpenRef.current = true;
    }

    if (hasOpenModal) {
      if (closeButtonRef.current && isElementVisible(closeButtonRef.current, panelRef.current)) {
        closeButtonRef.current.focus({ preventScroll: true });
      } else {
        panelRef.current?.focus({ preventScroll: true });
      }
    } else if (wasOpenRef.current) {
      const prev = previousFocusRef.current;
      previousFocusRef.current = null;
      wasOpenRef.current = false;
      restorePreviousFocus(prev, reportFrameRef.current);
    }
  }, [hasOpenModal, modalKind]);

  useEffect(() => {
    if (!hasOpenModal) return undefined;
    const handleKeyDown = (event) => {
      if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
      if (event.key === 'Escape') {
        if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
        event.preventDefault();
        setActiveModal(null);
        return;
      }
      if (event.key === 'Tab') {
        handleFocusTrapTab(event, panelRef.current);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [hasOpenModal]);

  const closeModal = () => setActiveModal(null);

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

      {activeModal?.kind === 'plot' ? (
        <ReportPreviewModal
          ariaLabel="Resistance plot"
          closeLabel="Close resistance plot"
          panelRef={panelRef}
          closeButtonRef={closeButtonRef}
          onClose={closeModal}
        >
          <img src={activeModal.src} alt={activeModal.alt} className="report-preview-plot-image" />
        </ReportPreviewModal>
      ) : null}

      {activeModal?.kind === 'structure' ? (
        <ReportPreviewModal
          ariaLabel={`Chemical structure of ${activeModal.title}`}
          closeLabel="Close chemical structure"
          panelClassName="report-preview-structure-panel"
          panelRef={panelRef}
          closeButtonRef={closeButtonRef}
          onClose={closeModal}
        >
          <h2 className="report-preview-structure-title">{activeModal.title}</h2>
          <img
            src={activeModal.src}
            alt={`Chemical structure of ${activeModal.title}`}
            className="report-preview-plot-image"
          />
        </ReportPreviewModal>
      ) : null}

      {activeModal?.kind === 'sequence' ? (
        <ReportPreviewModal
          ariaLabel="Feature sequence"
          closeLabel="Close feature sequence"
          panelClassName="report-preview-sequence-panel"
          panelRef={panelRef}
          closeButtonRef={closeButtonRef}
          onClose={closeModal}
        >
          <div className="report-preview-sequence-header">
            <h2 className="report-preview-sequence-title">{activeModal.title}</h2>
            <div className="report-preview-sequence-toggle" role="group" aria-label="Sequence view">
              <button
                type="button"
                className={`seq-toggle-btn${sequenceView === 'nt' ? ' seq-toggle-btn--active' : ''}`}
                aria-pressed={sequenceView === 'nt'}
                disabled={!activeModal.ntSequence}
                onClick={() => setSequenceView('nt')}
              >
                DNA
              </button>
              <button
                type="button"
                className={`seq-toggle-btn${sequenceView === 'aa' ? ' seq-toggle-btn--active' : ''}`}
                aria-pressed={sequenceView === 'aa'}
                disabled={!activeModal.aaSequence}
                onClick={() => setSequenceView('aa')}
              >
                Protein
              </button>
            </div>
          </div>
          <pre className="report-preview-sequence-block">
            {sequenceView === 'nt' ? activeModal.ntSequence : activeModal.aaSequence}
          </pre>
        </ReportPreviewModal>
      ) : null}
    </>
  );
});
