// Atlas Toolkit UI module
// --- Data State ---
var regionsData = [];
var selectedIndices = new Set();
var lastClickIndex = -1;
var isDragSelecting = false;
var dragStartIndex = -1;
var currentMode = "extract"; // 'extract' | 'modify'
var modifyRegionBounds = {}; // {name: [x, y, w, h, rotate], ...} — atlas bounds
var modifyOverlayRects = {}; // {name: [x, y, w, h], ...} — pre-computed draw rects
var hasModImage = false;
// Multi-page modify state
var modifyPages = []; // ordered page filenames
var modifyActivePageIndex = 0;
var modifyRegionPages = {}; // {name: pageFilename}
var modifiedRegionNames = new Set(); // regions already modified this session
var viewState = {
  scale: 1,
  x: 0,
  y: 0,
  isDragging: false,
  startX: 0,
  startY: 0,
};
