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
