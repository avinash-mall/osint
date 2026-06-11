// Ambient declaration for the Leaflet.VectorGrid plugin (no @types package
// ships for it). Importing 'leaflet.vectorgrid' for its side effect patches
// the global `L` with `L.vectorGrid.protobuf(...)`. We type the module as a
// side-effect import and reach into the patched `L` via `(L as any)` at the
// call site (DetectionTileLayer.tsx), so a minimal declaration is enough to
// satisfy `verbatimModuleSyntax` + `moduleDetection: force`.
declare module 'leaflet.vectorgrid';
