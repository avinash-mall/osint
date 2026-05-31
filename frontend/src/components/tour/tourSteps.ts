/**
 * TOUR_STEPS — declarative walkthrough of every interactive control on the
 * Map workspace. Targets are matched via `[data-tour="<id>"]` attributes
 * scattered across MapStage, LayerPanel, SelectionPanel, AnalyticsToolsPanel,
 * TimeMachineBar, and GaiaMap-level chrome (suppression chips, event
 * timeline).
 *
 * Each step body follows a consistent shape:
 *   First sentence: what the control does.
 *   Second sentence: when an analyst would reach for it.
 *
 * Steps whose target isn't currently in the DOM (e.g. SelectionPanel only
 * mounts when the right rail is open; AnalyticsToolsPanel only renders when
 * the ANALYTICS tab is active) are auto-skipped by ProductTour. Prerequisite
 * state (open the right panel, switch tabs) is satisfied by the
 * `onStepChange` callback wired in GaiaMap.
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
  // ─────────────────────────────────────────────────────────────────────
  // Left rail — Operating Picture
  // ─────────────────────────────────────────────────────────────────────
  {
    id: 'layer-panel',
    selector: '[data-tour="layer-panel"]',
    title: 'Operating picture',
    body: 'The left rail is the operating picture — every layer that paints on the map (basemaps, imagery, detection classes, analytics) is controlled from here. Reach for it whenever you want to focus the view on a specific category of intelligence.',
    placement: 'right',
  },
  {
    id: 'basemap-selector',
    selector: '[data-tour="basemap-selector"]',
    title: 'Basemap',
    body: 'Pick the cartographic base: SAT (imagery only), BASE (Carto dark vector), or TERRAIN (hillshade). Use BASE or TERRAIN when you need geographic reference points around your imagery, and SAT when only the raw scene matters.',
    placement: 'right',
  },
  {
    id: 'opacity-slider',
    selector: '[data-tour="opacity-slider"]',
    title: 'Imagery opacity',
    body: 'Fades the BASE or TERRAIN basemap layered on top of the imagery (disabled in SAT mode and past zoom 14). Slide left to clear cartography and read the raw raster; slide right to overlay labels/roads on dense scenes.',
    placement: 'right',
  },
  {
    id: 'layer-toggles',
    selector: '[data-tour="layer-toggles"]',
    title: 'Layer toggles',
    body: 'Show or hide each map layer: Satellite imagery, AI Detections, Active Tracks, Static features, Borders, and the MGRS Graticule. Useful for de-cluttering — e.g. hide tracks during a static-target sweep, or hide detections to study terrain before classification.',
    placement: 'right',
  },
  {
    id: 'geom-hbb',
    selector: '[data-tour="geom-hbb"]',
    title: 'HBB',
    body: 'Renders detection boxes as axis-aligned rectangles around the polygon. Easiest to skim across a busy AOI when precise object orientation does not matter.',
    placement: 'right',
  },
  {
    id: 'geom-obb',
    selector: '[data-tour="geom-obb"]',
    title: 'OBB (default)',
    body: 'Oriented bounding box drawn from SAM3 metadata — a tighter fit on rotated vehicles, vessels, and aircraft. The default mode; switch back to it whenever you need angular cues for tasking or classification.',
    placement: 'right',
  },
  {
    id: 'geom-mask',
    selector: '[data-tour="geom-mask"]',
    title: 'Mask',
    body: 'Shows the raw mask polygon — the exact pixel boundary the model produced. Reach for it when judging segmentation quality or measuring true object extent for size estimation.',
    placement: 'right',
  },
  {
    id: 'prithvi-flood',
    selector: '[data-tour="prithvi-flood"]',
    title: 'Prithvi flood overlay',
    body: 'Toggles the Prithvi-EO-2.0 flood head over the current imagery — colours water-inundated pixels distinct from normal surface water. Use during humanitarian assistance / disaster relief tasks to triage affected areas.',
    placement: 'right',
  },
  {
    id: 'prithvi-burn',
    selector: '[data-tour="prithvi-burn"]',
    title: 'Prithvi burn overlay',
    body: 'Burned-area segmentation from Prithvi-EO-2.0 — highlights fire-damaged ground. Useful for damage assessment after wildfires or strike events.',
    placement: 'right',
  },
  {
    id: 'prithvi-crops',
    selector: '[data-tour="prithvi-crops"]',
    title: 'Prithvi crops overlay',
    body: 'Multi-temporal crop classification from Prithvi-EO-2.0. Use to identify cultivated land vs bare ground vs structures when assessing rural areas for force-laydown context.',
    placement: 'right',
  },
  {
    id: 'detection-classes',
    selector: '[data-tour="detection-classes"]',
    title: 'Detection classes',
    body: 'The full taxonomy of classes the model emitted for the current view, grouped by CATegory or SouRCe. Toggle individual classes, solo a single class with click, or search for one — essential for focusing on a specific target type without rebuilding queries.',
    placement: 'right',
  },
  {
    id: 'imagery-list',
    selector: '[data-tour="imagery-list"]',
    title: 'Imagery passes',
    body: 'Lists the satellite scenes available for the AOR. Click a row to load that pass under the map and run detections against it — your primary way to switch between collection windows.',
    placement: 'right',
  },
  {
    id: 'imagery-delete',
    selector: '[data-tour="imagery-delete"]',
    title: 'Delete an imagery pass',
    body: 'Admins can permanently remove a scene — its detections, the graph nodes, and the on-disk file — after a confirmation. Use to clear test uploads or superseded collections; this cannot be undone.',
    placement: 'right',
  },
  {
    id: 'analytics-tools',
    selector: '[data-tour="analytics-tools"]',
    title: 'Analytics layer toggles',
    body: 'Visibility toggles for the output of Viewshed, Line of Sight, and Routes runs — locked until you actually run the tool from the ANALYTICS tab on the right. Use these to flip analytics overlays on/off without re-running them.',
    placement: 'right',
  },

  // ─────────────────────────────────────────────────────────────────────
  // Top action bar
  // ─────────────────────────────────────────────────────────────────────
  {
    id: 'draw-object',
    selector: '[data-tour="draw-object"]',
    title: 'Draw object',
    body: 'Click to enter draw mode, then drag a rectangle on the map to manually label an object the model missed. Useful for ground-truth contributions and forcing a track on a missed target.',
    placement: 'bottom',
  },
  {
    id: 'range-ring',
    selector: '[data-tour="range-ring"]',
    title: 'Range rings',
    body: 'Click to enter ring mode, pick a centre point on the map, and drop concentric range circles (in km) around it. Useful for visualising weapon-system reach, sensor footprint, or proximity buffers.',
    placement: 'bottom',
  },
  {
    id: 'product-tour-btn',
    selector: '[data-tour="product-tour-btn"]',
    title: 'Re-take the tour',
    body: 'Re-opens this guided walkthrough at any time. Use it after a major UI update, or when onboarding a teammate at your workstation.',
    placement: 'bottom',
  },

  // ─────────────────────────────────────────────────────────────────────
  // Right-side zoom cluster (now at the bottom of the right sidebar)
  // ─────────────────────────────────────────────────────────────────────
  {
    id: 'zoom-in',
    selector: '[data-tour="zoom-in"]',
    title: 'Zoom in (=)',
    body: 'Zoom one level closer on the map (= or + on the keyboard). Use to inspect detection geometry, individual vehicles, or small ground features.',
    placement: 'left',
  },
  {
    id: 'zoom-out',
    selector: '[data-tour="zoom-out"]',
    title: 'Zoom out (-)',
    body: 'Zoom one level out (- on the keyboard). Use to broaden situational awareness and put a target back into AOR context.',
    placement: 'left',
  },
  {
    id: 'recenter',
    selector: '[data-tour="recenter"]',
    title: 'Recenter (0)',
    body: 'Snap the map back to the starting view (0 on the keyboard). Quick way to recover after panning far away.',
    placement: 'left',
  },
  {
    id: 'focus-mode',
    selector: '[data-tour="focus-mode"]',
    title: 'Focus mode (F)',
    body: 'Collapses every floating chrome panel to a 24-pixel hover lip, giving you a near-full-bleed map (F on the keyboard). Use during briefings or when studying imagery without distractions.',
    placement: 'left',
  },
  {
    id: 'visual-mode',
    selector: '[data-tour="visual-mode"]',
    title: 'Tactical visual mode',
    body: 'Cycles a cosmetic colour filter over the map: DEFAULT → FLIR (thermal) → NVG (night-vision) → CRT (retro phosphor). Purely a display aid for low-light briefings or screenshots; it changes no data.',
    placement: 'left',
  },

  // ─────────────────────────────────────────────────────────────────────
  // Time-machine deep
  // ─────────────────────────────────────────────────────────────────────
  {
    id: 'time-machine',
    selector: '[data-tour="time-machine"]',
    title: 'Time machine',
    body: 'The time-machine scrubber and its filters live here — your control surface for "what was visible when". Drag the playhead, press play to step automatically, or use the controls covered in the next steps.',
    placement: 'top',
  },
  {
    id: 'tm-play',
    selector: '[data-tour="tm-play"]',
    title: 'Play / pause',
    body: 'Plays the time-machine — advances the playhead automatically through imagery passes within the current window. Use to watch a scene evolve hands-free during a brief.',
    placement: 'top',
  },
  {
    id: 'tm-recenter',
    selector: '[data-tour="tm-recenter"]',
    title: 'Recenter playhead',
    body: 'Snaps the playhead back to "now" (rightmost position). Use when you have scrubbed back into history and want to jump to live again.',
    placement: 'top',
  },
  {
    id: 'tm-ranges',
    selector: '[data-tour="tm-ranges"]',
    title: '24h / 7d / 30d window',
    body: 'Selects how much history the scrubber rail spans. Widen to 7d or 30d when investigating a slow-moving change (e.g. construction progress) and narrow to 24h for tactical recency.',
    placement: 'top',
  },
  {
    id: 'tm-conf',
    selector: '[data-tour="tm-conf"]',
    title: 'CONF threshold',
    body: 'Drops detections below this confidence floor from the map. Slide right to tighten the view to high-confidence calls only; the SHOWING N/M chip updates live so you can see exactly what you are suppressing.',
    placement: 'top',
  },
  {
    id: 'tm-passes',
    selector: '[data-tour="tm-passes"]',
    title: 'Pass count',
    body: 'Shows how many imagery acquisitions (the diamond markers on the scrubber) fall inside the current time window. Useful as a sanity check before scrubbing — zero passes means no imagery in this window.',
    placement: 'top',
  },
  {
    id: 'tm-legend',
    selector: '[data-tour="tm-legend"]',
    title: 'Sensor legend',
    body: 'Colour key for the diamond pass markers on the scrubber: RGB optical (blue), MSI multispectral (purple), SAR radar (orange), HSI hyperspectral (pink). Reach for it when triaging which scene to inspect — SAR is your foul-weather option, MSI exposes vegetation health, and so on.',
    placement: 'top',
  },

  // ─────────────────────────────────────────────────────────────────────
  // Bottom chrome — suppression transparency + event timeline
  // ─────────────────────────────────────────────────────────────────────
  {
    id: 'hidden-banner',
    selector: '[data-tour="hidden-banner"]',
    title: 'Restored hidden filters',
    body: 'A one-shot reminder when the previous session left categories or labels hidden, so silent filters cannot mask new positives. Click "Show N hidden …" to restore visibility, or ✕ to dismiss.',
    placement: 'top',
  },
  {
    id: 'showing-chip',
    selector: '[data-tour="showing-chip"]',
    title: 'Showing N / M',
    body: 'Suppression transparency — shows how many detections are hidden and why (below confidence, by category, by label, by time window). Each chip with an ✕ is clickable to clear that one filter; the advisory chips just explain why some detections are excluded.',
    placement: 'top',
  },
  {
    id: 'event-timeline',
    selector: '[data-tour="event-timeline"]',
    title: 'Event timeline',
    body: 'A rolling histogram of detection activity over the last 60 minutes (orange = inside the current window, grey = outside). Use it to spot spikes in detection volume that might warrant a deeper look.',
    placement: 'top',
  },
  {
    id: 'event-windows',
    selector: '[data-tour="event-windows"]',
    title: '15M / 30M / 60M',
    body: 'Sets how far back the histogram colour-codes as "in window" — 15, 30, or 60 minutes. Narrow it when you only care about very recent detections; widen for a fuller activity picture.',
    placement: 'top',
  },
  {
    id: 'event-counter',
    selector: '[data-tour="event-counter"]',
    title: 'In-window count',
    body: 'Total number of detections inside the histogram window — the same value the map currently renders. Watch it tick as you adjust filters above to verify the change took effect.',
    placement: 'top',
  },

  // ─────────────────────────────────────────────────────────────────────
  // Right sidebar — SelectionPanel deep
  // ─────────────────────────────────────────────────────────────────────
  {
    id: 'selection-panel',
    selector: '[data-tour="selection-panel"]',
    title: 'Selection panel',
    body: 'The right rail — the "what about this thing?" workspace. Click any detection polygon on the map to populate it with details, analytics inputs, similar objects, and active tracks for that selection.',
    placement: 'left',
  },
  {
    id: 'selection-header-chip',
    selector: '[data-tour="selection-header-chip"]',
    title: 'Header status chip',
    body: 'Quick readout of the active tab — DETAIL / ANALYTICS / NEAREST / TRACKS. When a detection is selected on the DETAILS tab, it switches to the allegiance tag (FRIEND / HOSTILE / NEUTRAL / UNKNOWN) so you can see classification at a glance.',
    placement: 'left',
  },
  {
    id: 'selection-collapse',
    selector: '[data-tour="selection-collapse"]',
    title: 'Collapse the panel',
    body: 'Collapses the right rail to a thin vertical handle so the map can use the full width. Click the handle again to bring the panel back. Useful during full-screen briefs.',
    placement: 'left',
  },
  {
    id: 'tab-details',
    selector: '[data-tour="tab-details"]',
    title: 'Details tab',
    body: 'Shows the selected detection\'s classification, confidence, geolocation, allegiance, taxonomy, edit/review forms, candidate links, and quick actions (tag, delete, export, add to graph). Your primary work surface when triaging a single object.',
    placement: 'left',
  },
  {
    id: 'object-details-platform',
    selector: '[data-tour="object-details-platform"]',
    title: 'Platform identity',
    body: 'The platform_* fields — type, name, role, and source — assigned to this detection from the reference DB. Populated automatically when an identification candidate scores above the auto-apply threshold, or manually when you approve a candidate below.',
    placement: 'left',
  },
  {
    id: 'identification-panel',
    selector: '[data-tour="identification-panel"]',
    title: 'Platform identification',
    body: 'Top reference-DB platform candidates for this detection. Auto-applied if score ≥ 0.85 (configurable via REFERENCE_ID_AUTO_THRESHOLD). Use Approve to lock the analyst-asserted identity, Reject to discard, or Re-identify to re-run the lookup.',
    placement: 'left',
  },
  {
    id: 'tab-analytics',
    selector: '[data-tour="tab-analytics"]',
    title: 'Analytics tab',
    body: 'Houses the three on-demand analytics tools (Viewshed, Line of Sight, Routes). Switch to it whenever you need terrain-aware reasoning about a point on the map.',
    placement: 'left',
  },
  {
    id: 'analytics-viewshed',
    selector: '[data-tour="analytics-viewshed"]',
    title: 'Viewshed',
    body: 'Pick an observer point and a radius; the tool calls the DEM-backed /api/analytics/viewshed and overlays everywhere visible from that observer at the given height. Use to plan sensor placement or check if a candidate target is observable from a known OP.',
    placement: 'left',
  },
  {
    id: 'analytics-los',
    selector: '[data-tour="analytics-los"]',
    title: 'Line of sight',
    body: 'Pick an observer (OBS) and a target (TGT); the tool computes whether terrain blocks the sight line between them and renders per-obstruction Point features along the path. Use to validate sensor / shooter geometry before action.',
    placement: 'left',
  },
  {
    id: 'analytics-routes',
    selector: '[data-tour="analytics-routes"]',
    title: 'Routes',
    body: 'Pick a start and end point, then run a route over the OSMnx routing graph. Strategy presets — Shortest, Least exposure, Balanced, COVER · DEM — let you trade off speed against survivability.',
    placement: 'left',
  },
  {
    id: 'analytics-capabilities',
    selector: '[data-tour="analytics-capabilities"]',
    title: 'DEM · Routing graph status',
    body: 'Tells you whether the backend has a DEM and an OSMnx routing graph loaded. If either says NONE, the affected tools fall back to canned-shape fixtures — useful sanity check when results look suspicious.',
    placement: 'left',
  },
  {
    id: 'tab-satellites',
    selector: '[data-tour="tab-satellites"]',
    title: 'Satellites tab',
    body: 'Plan collection windows offline: import TLEs (air-gap), pick an observer on the map, and predict upcoming overpasses with AOS/LOS and max elevation. Draw a satellite ground track on the map. Reach for it when scheduling the next pass over an AOI.',
    placement: 'left',
  },
  {
    id: 'tab-similar',
    selector: '[data-tour="tab-similar"]',
    title: 'Similar tab',
    body: 'Nearest-neighbour detections by DINOv3-SAT embedding for the current selection. Use to find more instances of the same object across the AOR, or to confirm a tentative classification by visual analogy.',
    placement: 'left',
  },
  {
    id: 'tab-tracks',
    selector: '[data-tour="tab-tracks"]',
    title: 'Active Tracks tab',
    body: 'Lists every active satellite-pass-stitched track in the workspace, with its callsign / asset ID and a LIVE tag. Switch here to manage tracked targets across multiple passes.',
    placement: 'left',
  },
  {
    id: 'tracks-track-object',
    selector: '[data-tour="tracks-track-object"]',
    title: 'Track Object',
    body: 'Force-creates a new track from the currently selected detection. Use when the auto-tracker missed an obvious continuation and you want to pin a target manually.',
    placement: 'left',
  },
];
