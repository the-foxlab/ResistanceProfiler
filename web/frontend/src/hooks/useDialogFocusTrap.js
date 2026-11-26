const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

export function isElementVisible(el, container) {
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

export function getFocusableElements(container) {
  if (!container) return [];
  return Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR)).filter(
    (el) => isElementVisible(el, container),
  );
}

export function isRestorableTrigger(el, ignoreEl = null) {
  if (!el || !el.isConnected || typeof el.focus !== 'function') return false;
  if (el === document.body || el === document.documentElement) return false;
  if (el === ignoreEl || el.tagName === 'IFRAME') return false;
  return isElementVisible(el, document.documentElement);
}

export function restorePreviousFocus(previousEl, ignoreEl = null) {
  if (isRestorableTrigger(previousEl, ignoreEl)) {
    previousEl.focus({ preventScroll: true });
    if (document.activeElement === previousEl) return;
  }
  const mainFallback = document.querySelector('main[tabindex], #main-content[tabindex]');
  if (mainFallback && typeof mainFallback.focus === 'function') {
    mainFallback.focus({ preventScroll: true });
  }
}

/**
 * Constrain Tab / Shift+Tab navigation within *container*.
 */
export function handleFocusTrapTab(event, container) {
  if (!container || event.altKey || event.ctrlKey || event.metaKey) return;
  const focusable = getFocusableElements(container);
  if (focusable.length === 0) {
    event.preventDefault();
    container.focus({ preventScroll: true });
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const active = document.activeElement;
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
