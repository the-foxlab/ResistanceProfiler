import packageJson from '../package.json';
import { API_BASE } from './api';
import { sanitizeEmailAddress, sanitizeHttpUrl } from './utils';

export const webVersion = packageJson.version;

export function _resolveLegalLink(legalData) {
  if (!legalData || !legalData.enabled) return null;
  if (legalData.kind === 'url') {
    const rawUrl = legalData.url === null || legalData.url === undefined ? '' : String(legalData.url).trim();
    if (!rawUrl) return `${API_BASE}/legal`;
    return sanitizeHttpUrl(rawUrl);
  }
  return `${API_BASE}/legal`;
}

export function _resolveContactEmail(contactData) {
  if (!contactData || !contactData.enabled || !contactData.email) return null;
  return sanitizeEmailAddress(contactData.email);
}
