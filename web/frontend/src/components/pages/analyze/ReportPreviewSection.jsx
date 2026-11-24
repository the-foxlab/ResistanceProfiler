import { useContext, useEffect, useRef, useState } from 'react';

import { ThemeContext } from '../../../hooks/useTheme';

function ReportPreviewModal({ ariaLabel, closeLabel, panelClassName = '', onClose, children }) {
  return (
    <div className="report-preview-plot-modal" role="dialog" aria-modal="true" aria-label={ariaLabel}>
      <div className="report-preview-plot-backdrop" onClick={onClose} aria-hidden="true" />
      <section className={`report-preview-plot-panel${panelClassName ? ` ${panelClassName}` : ''}`}>
        <button
          type="button"
          className="report-preview-plot-close"
          aria-label={closeLabel}
          onClick={onClose}
          autoFocus
        >
          &times;
        </button>
        {children}
      </section>
    </div>
  );
}

export function ReportPreviewSection({ showFrame, inlineReportPath, buildReportUrl }) {
  const resolvedTheme = useContext(ThemeContext);
  const reportFrameRef = useRef(null);
  const [reportFrameHeight, setReportFrameHeight] = useState(null);
  const [hostedPlot, setHostedPlot] = useState(null);
  const [hostedStructure, setHostedStructure] = useState(null);
  const [hostedSequence, setHostedSequence] = useState(null);
  const [sequenceView, setSequenceView] = useState('nt');

  // Resolve against window.location.href so same-origin relative report URLs
  // still yield a valid origin on remount before reportFrameRef.current is set.
  const reportOrigin = (() => {
    try {
      const src = reportFrameRef.current?.src || (inlineReportPath ? buildReportUrl(inlineReportPath) : '');
      return src ? new URL(src, window.location.href).origin : '';
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
        reportOrigin || window.location.origin,
      );
    } catch {
      // The iframe load handler retries once the report listener is ready.
    }
  }, [resolvedTheme, inlineReportPath, reportOrigin]);

  useEffect(() => {
    setReportFrameHeight(null);
    setHostedPlot(null);
  }, [inlineReportPath]);

  useEffect(() => {
    const handleMessage = (event) => {
      if (event.origin !== reportOrigin) return;
      if (event.source !== reportFrameRef.current?.contentWindow) return;

      const { type } = event.data || {};
      if (type === 'respro:open-plot') {
        if (event.data.src) {
          setHostedPlot({ src: event.data.src, alt: event.data.alt || 'Resistance plot' });
        }
      } else if (type === 'respro:open-structure') {
        if (event.data.src) {
          setHostedStructure({ src: event.data.src, title: event.data.title || 'Structure' });
        }
      } else if (type === 'respro:open-sequence') {
        setHostedSequence({
          title: event.data.title || 'Feature sequence',
          ntSequence: event.data.ntSequence || '',
          aaSequence: event.data.aaSequence || '',
        });
        setSequenceView(event.data.ntSequence ? 'nt' : 'aa');
      } else if (type === 'respro:report-height' && typeof event.data.height === 'number' && event.data.height > 0) {
        setReportFrameHeight(event.data.height + 2);
      }
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [reportOrigin]);

  const hasOpenModal = Boolean(hostedPlot || hostedStructure || hostedSequence);
  useEffect(() => {
    if (!hasOpenModal) return undefined;
    const closeOnEscape = (event) => {
      if (event.key !== 'Escape') return;
      setHostedPlot(null);
      setHostedStructure(null);
      setHostedSequence(null);
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [hasOpenModal]);

  return (
    <>
      {showFrame ? (
        <article className="full-width-tile tab-primary-tile">
          <iframe
            ref={reportFrameRef}
            title="ResistanceProfiler report"
            src={buildReportUrl(inlineReportPath)}
            className="workspace-frame"
            sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-downloads"
            style={reportFrameHeight ? { height: `${reportFrameHeight}px` } : undefined}
            onLoad={(event) => {
              event.currentTarget.contentWindow?.postMessage(
                { type: 'respro:report-theme', theme: resolvedTheme },
                reportOrigin || window.location.origin,
              );
              try {
                const frameDoc = event.currentTarget.contentDocument;
                if (!frameDoc?.body || !frameDoc.documentElement) return;
                const nextHeight = Math.max(
                  frameDoc.body.scrollHeight,
                  frameDoc.documentElement.scrollHeight,
                );
                setReportFrameHeight(nextHeight + 2);
              } catch {
                // Cross-origin (dev mode): wait for respro:report-height message.
                setReportFrameHeight(null);
              }
            }}
          />
        </article>
      ) : null}

      {hostedPlot ? (
        <ReportPreviewModal
          ariaLabel="Resistance plot"
          closeLabel="Close resistance plot"
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
          onClose={() => setHostedSequence(null)}
        >
          <div className="report-preview-sequence-header">
            <h2 className="report-preview-sequence-title">{hostedSequence.title}</h2>
            <div className="report-preview-sequence-toggle">
              <button
                type="button"
                className={`seq-toggle-btn${sequenceView === 'nt' ? ' seq-toggle-btn--active' : ''}`}
                disabled={!hostedSequence.ntSequence}
                onClick={() => setSequenceView('nt')}
              >
                DNA
              </button>
              <button
                type="button"
                className={`seq-toggle-btn${sequenceView === 'aa' ? ' seq-toggle-btn--active' : ''}`}
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
}
