import { useEffect } from 'react';
import { API_BASE, chunkIds, getArtifactBundleLimit } from '../api';

/** Best-effort cleanup of session-owned uploads and artifacts when the page unloads. */
export function useSessionCleanup(uploadedPaths, reportPaths) {
  useEffect(() => {
    const cleanupUrl = `${API_BASE}/api/session/cleanup`;

    const handlePageHide = (event) => {
      if (event.persisted || (uploadedPaths.length === 0 && reportPaths.length === 0)) {
        return;
      }
      const limit = getArtifactBundleLimit();
      const uploadChunks = chunkIds(uploadedPaths, limit);
      const artifactChunks = chunkIds(reportPaths, limit);
      const requestCount = Math.max(uploadChunks.length, artifactChunks.length);
      if (requestCount > 1) {
        console.warn('Session cleanup needs multiple requests; rate limits may prevent cleanup.');
      }

      for (let index = 0; index < requestCount; index += 1) {
        const payload = JSON.stringify({
          upload_ids: uploadChunks[index] || [],
          artifact_ids: artifactChunks[index] || [],
        });
        if (navigator.sendBeacon) {
          const blob = new Blob([payload], { type: 'application/json' });
          try {
            if (navigator.sendBeacon(cleanupUrl, blob)) continue;
            console.warn('Session cleanup beacon was rejected; attempting keepalive fetch.');
          } catch (error) {
            console.warn('Session cleanup beacon failed; attempting keepalive fetch.', error);
          }
        }
        try {
          fetch(cleanupUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: payload,
            keepalive: true,
          })
            .then((response) => {
              if (!response.ok) console.warn(`Session cleanup failed with HTTP ${response.status}.`);
            })
            .catch((error) => console.warn('Session cleanup keepalive request failed.', error));
        } catch (error) {
          console.warn('Session cleanup keepalive request failed.', error);
        }
      }
    };

    window.addEventListener('pagehide', handlePageHide);
    return () => window.removeEventListener('pagehide', handlePageHide);
  }, [uploadedPaths, reportPaths]);
}
