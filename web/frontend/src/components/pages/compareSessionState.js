/**
 * Session-persistent state for the Compare Databases tab.
 *
 * The tab unmounts whenever the user switches sidebar modes, so this module-level
 * store keeps the selection and result alive for the browser session.
 */

function initialState() {
  return {
    slots: ['', '', ''],
    accession: '',
    response: null,
    regionFilter: 'all',
    expandedKey: '',
    tableSearch: '',
    sharedRefs: [],
    sharedRefsKey: '',
  };
}

let state = initialState();

export function getCompareState() {
  return state;
}

export function setCompareState(patch) {
  state = { ...state, ...patch };
}

export function resetCompareState() {
  state = initialState();
}
