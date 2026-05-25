/**
 * TOUR_STEPS — declarative walkthrough of every interactive control on the
 * Map workspace. Targets are matched via `[data-tour="<id>"]` attributes
 * scattered across MapStage, LayerPanel, SelectionPanel, TimeMachineBar.
 *
 * Steps whose target isn't currently in the DOM (e.g. SelectionPanel only
 * mounts when a detection is selected) are auto-skipped by ProductTour.
 */

export type Placement = 'top' | 'bottom' | 'left' | 'right';

export type TourStep = {
  id: string;
  selector: string;
  title: string;
  body: string;
  placement: Placement;
};

export const TOUR_STEPS: TourStep[] = [
  // — left rail: operating picture —
  {
    id: 'layer-panel',
    selector: '[data-tour="layer-panel"]',
    title: 'Operating picture',
    body: 'Left rail. Controls every layer on the map: basemaps, imagery, detection classes, analytics.',
    placement: 'right',
  },
  {
    id: 'basemap-selector',
    selector: '[data-tour="basemap-selector"]',
    title: 'Basemap',
    body: 'Pick Sat (imagery only), Base (Carto dark vector), or Terrain (hillshade).',
    placement: 'right',
  },
  {
    id: 'opacity-slider',
    selector: '[data-tour="opacity-slider"]',
    title: 'Imagery opacity',
    body: 'Fades the reference basemap on top of the imagery. Disabled in Sat mode and past zoom 14.',
    placement: 'right',
  },
  {
    id: 'layer-toggles',
    selector: '[data-tour="layer-toggles"]',
    title: 'Layer toggles',
    body: 'Show/hide Satellite, Detections, Tracks, Static features, Borders, Graticule.',
    placement: 'right',
  },
  {
    id: 'detection-classes',
    selector: '[data-tour="detection-classes"]',
    title: 'Detection classes',
    body: 'Filter detections by class. Group by CAT or SRC. Search, hide individual classes, solo a class.',
    placement: 'right',
  },
  {
    id: 'imagery-list',
    selector: '[data-tour="imagery-list"]',
    title: 'Imagery passes',
    body: 'Available satellite scenes. Click one to load it under the map.',
    placement: 'right',
  },
  {
    id: 'analytics-tools',
    selector: '[data-tour="analytics-tools"]',
    title: 'Analytics tools',
    body: 'Viewshed, line-of-sight, route planning. Run from here; results overlay on the map.',
    placement: 'right',
  },

  // — top-center toolbar —
  {
    id: 'geom-hbb',
    selector: '[data-tour="geom-hbb"]',
    title: 'HBB',
    body: 'Axis-aligned bounding box around detections.',
    placement: 'bottom',
  },
  {
    id: 'geom-obb',
    selector: '[data-tour="geom-obb"]',
    title: 'OBB (default)',
    body: 'Oriented bounding box from SAM3 metadata — tighter fit on rotated objects.',
    placement: 'bottom',
  },
  {
    id: 'geom-mask',
    selector: '[data-tour="geom-mask"]',
    title: 'Mask',
    body: 'Raw mask polygon — exact pixel boundary.',
    placement: 'bottom',
  },
  {
    id: 'prithvi-flood',
    selector: '[data-tour="prithvi-flood"]',
    title: 'Prithvi flood overlay',
    body: 'Toggle the Prithvi-EO-2.0 flood head over the current imagery.',
    placement: 'bottom',
  },
  {
    id: 'prithvi-burn',
    selector: '[data-tour="prithvi-burn"]',
    title: 'Prithvi burn overlay',
    body: 'Burned-area segmentation from Prithvi-EO-2.0.',
    placement: 'bottom',
  },
  {
    id: 'prithvi-crops',
    selector: '[data-tour="prithvi-crops"]',
    title: 'Prithvi crops overlay',
    body: 'Multi-temporal crop classification from Prithvi-EO-2.0.',
    placement: 'bottom',
  },
  {
    id: 'tracks-toggle',
    selector: '[data-tour="tracks-toggle"]',
    title: 'Asset tracks',
    body: 'Toggle satellite-pass-stitched asset tracks.',
    placement: 'bottom',
  },
  {
    id: 'draw-object',
    selector: '[data-tour="draw-object"]',
    title: 'Draw object',
    body: 'Manually box an object on the map and label it. Useful for ground-truth.',
    placement: 'bottom',
  },
  {
    id: 'range-ring',
    selector: '[data-tour="range-ring"]',
    title: 'Range rings',
    body: 'Drop concentric range rings (km) around a clicked point.',
    placement: 'bottom',
  },
  {
    id: 'product-tour-btn',
    selector: '[data-tour="product-tour-btn"]',
    title: 'Re-take the tour',
    body: 'Click here any time to re-open this walkthrough.',
    placement: 'bottom',
  },

  // — right-side zoom cluster —
  {
    id: 'zoom-in',
    selector: '[data-tour="zoom-in"]',
    title: 'Zoom in (=)',
    body: 'Zoom into the map.',
    placement: 'left',
  },
  {
    id: 'zoom-out',
    selector: '[data-tour="zoom-out"]',
    title: 'Zoom out (-)',
    body: 'Zoom out.',
    placement: 'left',
  },
  {
    id: 'recenter',
    selector: '[data-tour="recenter"]',
    title: 'Recenter (0)',
    body: 'Snap back to the starting view.',
    placement: 'left',
  },
  {
    id: 'focus-mode',
    selector: '[data-tour="focus-mode"]',
    title: 'Focus mode (F)',
    body: 'Collapse all chrome and study the map full-bleed.',
    placement: 'left',
  },

  // — bottom + right —
  {
    id: 'time-machine',
    selector: '[data-tour="time-machine"]',
    title: 'Time Machine',
    body: 'Temporal slider over satellite passes. Scrubs detections in/out by acquisition window.',
    placement: 'top',
  },
  {
    id: 'selection-panel',
    selector: '[data-tour="selection-panel"]',
    title: 'Selection panel',
    body: 'Click a detection on the map to populate Details / Analytics / Similar / Actions tabs here.',
    placement: 'left',
  },
];
