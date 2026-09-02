/**
 * App footer: optional Legal notice and Contact links plus version spans.
 *
 * Each neighbour pair is separated by a ``·`` only when both are present, so the
 * separator count equals the number of adjacent present items minus one. The
 * order is: Legal notice · Contact · Core version · WebApp version.
 */
export function AppFooter({ legalLink, contactEmail, cliVersion, webVersion }) {
  return (
    <footer className="app-footer">
      {(legalLink || contactEmail) && (
        <div className="app-footer-group">
          {legalLink && (
            <a href={legalLink} target="_blank" rel="noreferrer">Legal notice</a>
          )}
          {legalLink && contactEmail && (
            <span className="app-footer-sep" aria-hidden="true">·</span>
          )}
          {contactEmail && (
            <a href={`mailto:${contactEmail}`}>Contact</a>
          )}
        </div>
      )}
      {(cliVersion || webVersion) && (
        <div className="app-footer-group">
          {cliVersion && (
            <span className="app-footer-version">ResistanceProfiler Core v{cliVersion}</span>
          )}
          {cliVersion && webVersion && (
            <span className="app-footer-sep" aria-hidden="true">·</span>
          )}
          {webVersion && (
            <span className="app-footer-version">ResistanceProfiler WebApp v{webVersion}</span>
          )}
        </div>
      )}
    </footer>
  );
}
