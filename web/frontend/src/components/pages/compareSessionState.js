/**
 * Session-persistent state for the Compare Databases tab.
 *
 * The tab unmounts whenever the user switches sidebar modes, and React state
 * would be lost. This module-level store keeps the selection alive for the
 * browser session without adding a global provider: the tab hydrates from it
 * on mount and writes through on every change.
 */

const EMPTY_SLOTS = ['', '', ''];

let state = {
  slots: [...EMPTY_SLOTS],
  accession: '',
  response: null,
  regionFilter: 'all',
  expandedKey: '',
};

export function getCompareState() {
  return state;
}

export function setCompareState(patch) {
  state = { ...state, ...patch };
}

export function resetCompareState() {
  state = {
    slots: [...EMPTY_SLOTS],
    accession: '',
    response: null,
    regionFilter: 'all',
    expandedKey: '',
  };
}
