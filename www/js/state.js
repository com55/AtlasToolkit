export const state = {
  regionsData: [],
  selectedIndices: new Set(),
  lastClickIndex: -1,
  isDragSelecting: false,
  dragStartIndex: -1,
  currentMode: 'extract',
  modifyRegionBounds: {},
  hasModImage: false,
  modifyPages: [],
  modifyRegionPages: {},
  modifyActivePage: null,
  modifyActivePageIndex: 0,
  viewState: { scale: 1, x: 0, y: 0, isDragging: false, startX: 0, startY: 0 },
};

export function getSelectedNames() {
  return Array.from(state.selectedIndices).sort((a, b) => a - b).map(i => state.regionsData[i]);
}

/**
 * Currently-selected regions as full { key, label } entries, in ascending
 * region-list index order (matches getSelectedNames()'s existing order —
 * NOT click/insertion order). See the region-identity-key-refactor spec's
 * "Global Constraints" for why this order is load-bearing.
 */
export function getSelectedRegions() {
  return Array.from(state.selectedIndices).sort((a, b) => a - b).map(i => state.regionsData[i]);
}

/** Engine-addressing keys only, for call sites that never render a label. */
export function getSelectedKeys() {
  return getSelectedRegions().map(r => r.key);
}

/** Display labels only, for call sites that never address the engine. */
export function getSelectedLabels() {
  return getSelectedRegions().map(r => r.label);
}
