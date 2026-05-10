/**
 * Curated icon library for the Sentinel ontology UI.
 *
 * Stable snake_case keys correspond 1:1 with the `icon_key` values produced by
 * `backend/scripts/seed_ontology.py` (ICON_RULES + BRANCH_ICON_KEY_FALLBACK).
 *
 * Use `IconRenderer` to render an icon in the UI. It performs a direct lookup
 * by `iconKey` first, then falls back to a branch-level `fallbackBranchKey`,
 * and finally returns the generic `CircleHelp` glyph when nothing matches.
 *
 * The legacy regex matcher in `branchIcons.tsx` (`objectIconComponent`) is kept
 * as a last-resort fallback for integrators that want to retrofit it; new code
 * should prefer this explicit map.
 */
import {
  Activity,
  Anchor,
  BrickWall,
  Building2,
  BusFront,
  Car,
  CircleHelp,
  CircleParking,
  Construction,
  Container,
  Crosshair,
  Dumbbell,
  EyeOff,
  Factory,
  Flame,
  Fuel,
  Helicopter,
  Landmark,
  Navigation,
  Package,
  Plane,
  Rocket,
  Sailboat,
  Shield,
  ShieldHalf,
  Ship,
  ShipWheel,
  TrainFront,
  Truck,
  Warehouse,
  Waves,
  Wheat,
  type LucideIcon,
} from 'lucide-react';
import type { JSX } from 'react';

export type IconCategory =
  | 'armored'
  | 'air_defense'
  | 'aircraft'
  | 'naval'
  | 'logistics'
  | 'infrastructure'
  | 'urban'
  | 'industrial'
  | 'damage'
  | 'vegetation'
  | 'recreation'
  | 'fortification'
  | 'civilian'
  | 'generic';

export interface IconEntry {
  /** Stable snake_case identifier — must match the `icon_key` emitted by the seed. */
  key: string;
  Component: LucideIcon;
  category: IconCategory;
  /** Helps the admin UI search and the migration auto-pick. */
  keywords: string[];
}

export const ICON_LIBRARY: IconEntry[] = [
  // ---------------------------------------------------------------------
  // Armored / ground combat
  // ---------------------------------------------------------------------
  { key: 'tank',           Component: ShieldHalf, category: 'armored',         keywords: ['tank', 'mbt', 'main battle tank'] },
  { key: 'shield',         Component: Shield,     category: 'armored',         keywords: ['armor', 'apc', 'ifv', 'armoured', 'warship', 'destroyer', 'frigate'] },

  // ---------------------------------------------------------------------
  // Air defense / radar / missiles
  // ---------------------------------------------------------------------
  { key: 'crosshair',      Component: Crosshair,  category: 'air_defense',     keywords: ['radar', 'sam', 'tel', 'launcher', 'jammer', 'antenna', 'elint'] },
  { key: 'rocket',         Component: Rocket,     category: 'air_defense',     keywords: ['missile', 'launcher', 'tel', 'silo', 'rocket', 'sam', 'icbm', 'irbm'] },

  // ---------------------------------------------------------------------
  // Aircraft / aviation
  // ---------------------------------------------------------------------
  { key: 'plane',          Component: Plane,      category: 'aircraft',        keywords: ['plane', 'aircraft', 'jet', 'fighter', 'bomber', 'drone', 'uav'] },
  { key: 'helicopter',     Component: Helicopter, category: 'aircraft',        keywords: ['helicopter', 'helipad', 'chopper'] },

  // ---------------------------------------------------------------------
  // Naval
  // ---------------------------------------------------------------------
  { key: 'ship',           Component: Ship,       category: 'naval',           keywords: ['ship', 'vessel', 'tanker', 'cargo'] },
  { key: 'ship_wheel',     Component: ShipWheel,  category: 'naval',           keywords: ['warship', 'frigate', 'destroyer', 'cruiser', 'cargo ship', 'oil tanker'] },
  { key: 'anchor',         Component: Anchor,     category: 'naval',           keywords: ['anchor', 'tugboat', 'barge', 'ferry', 'landing craft'] },
  { key: 'sailboat',       Component: Sailboat,   category: 'naval',           keywords: ['sailboat', 'yacht'] },

  // ---------------------------------------------------------------------
  // Logistics
  // ---------------------------------------------------------------------
  { key: 'package',        Component: Package,    category: 'logistics',       keywords: ['package', 'cargo', 'crate', 'supply'] },
  { key: 'truck',          Component: Truck,      category: 'logistics',       keywords: ['truck', 'lorry', 'transport', 'cargo truck', 'fuel truck'] },
  { key: 'container',      Component: Container,  category: 'logistics',       keywords: ['container', 'shipping container'] },
  { key: 'warehouse',      Component: Warehouse,  category: 'logistics',       keywords: ['warehouse', 'depot', 'hangar', 'aircraft shelter', 'supply depot'] },
  { key: 'fuel',           Component: Fuel,       category: 'logistics',       keywords: ['fuel', 'storage tank', 'oil tank', 'gas', 'lng', 'silo'] },
  { key: 'bus_front',      Component: BusFront,   category: 'logistics',       keywords: ['bus', 'bus terminal'] },
  { key: 'car',            Component: Car,        category: 'logistics',       keywords: ['car', 'vehicle', 'sedan', 'van', 'apc', 'ifv', 'tank', 'mortar'] },

  // ---------------------------------------------------------------------
  // Infrastructure / transport
  // ---------------------------------------------------------------------
  { key: 'navigation',     Component: Navigation, category: 'infrastructure',  keywords: ['bridge', 'overpass', 'highway', 'road', 'tunnel', 'interchange'] },
  { key: 'train_front',    Component: TrainFront, category: 'infrastructure',  keywords: ['train', 'railway', 'locomotive', 'rail yard'] },
  { key: 'circle_parking', Component: CircleParking, category: 'infrastructure', keywords: ['parking', 'lot', 'dealership', 'gas station'] },
  { key: 'construction',   Component: Construction, category: 'infrastructure', keywords: ['construction', 'crane', 'excavator', 'loader'] },

  // ---------------------------------------------------------------------
  // Industrial
  // ---------------------------------------------------------------------
  { key: 'factory',        Component: Factory,    category: 'industrial',      keywords: ['factory', 'powerplant', 'refinery', 'industrial', 'substation', 'cement', 'steel mill'] },

  // ---------------------------------------------------------------------
  // Urban / civilian buildings
  // ---------------------------------------------------------------------
  { key: 'building_2',     Component: Building2,  category: 'urban',           keywords: ['building', 'residential', 'office', 'apartment', 'mall', 'hospital', 'school'] },
  { key: 'landmark',       Component: Landmark,   category: 'urban',           keywords: ['landmark', 'installation', 'base', 'airport', 'terminal', 'runway', 'control tower', 'garrison'] },

  // ---------------------------------------------------------------------
  // Recreation / civilian
  // ---------------------------------------------------------------------
  { key: 'dumbbell',       Component: Dumbbell,   category: 'recreation',      keywords: ['stadium', 'sport', 'court', 'field', 'gym', 'park', 'parade ground'] },

  // ---------------------------------------------------------------------
  // Vegetation / nature / water
  // ---------------------------------------------------------------------
  { key: 'wheat',          Component: Wheat,      category: 'vegetation',      keywords: ['crop', 'farm', 'field', 'agriculture', 'aquaculture'] },
  { key: 'waves',          Component: Waves,      category: 'vegetation',      keywords: ['water', 'lake', 'pond', 'flood', 'dam'] },

  // ---------------------------------------------------------------------
  // Fortification
  // ---------------------------------------------------------------------
  { key: 'brick_wall',     Component: BrickWall,  category: 'fortification',   keywords: ['bunker', 'wall', 'trench', 'revetment', 'berm', 'hesco', 'sandbag', 'fence', 'gate', 'watchtower'] },

  // ---------------------------------------------------------------------
  // Damage
  // ---------------------------------------------------------------------
  { key: 'flame',          Component: Flame,      category: 'damage',          keywords: ['fire', 'burn', 'crater', 'damaged', 'destroyed', 'wreckage', 'demolished'] },

  // ---------------------------------------------------------------------
  // Generic / fallback
  // ---------------------------------------------------------------------
  { key: 'eye_off',        Component: EyeOff,     category: 'generic',         keywords: ['decoy', 'camouflage', 'concealed', 'dummy', 'deception'] },
  { key: 'activity',       Component: Activity,   category: 'generic',         keywords: ['track', 'activity', 'change', 'tracks', 'movement', 'plume'] },
  { key: 'circle_help',    Component: CircleHelp, category: 'generic',         keywords: ['unknown', 'other', 'help'] },
];

export const ICON_BY_KEY: Record<string, IconEntry> = Object.fromEntries(
  ICON_LIBRARY.map((entry) => [entry.key, entry]),
);

/** Convenience: lookup the lucide component by key, or null if absent. */
export function iconComponentByKey(key: string | null | undefined): LucideIcon | null {
  if (!key) return null;
  const entry = ICON_BY_KEY[key];
  return entry ? entry.Component : null;
}

export interface IconRendererProps {
  /** Preferred — a snake_case key from the curated library. */
  iconKey?: string | null;
  /** Branch-level fallback (also a curated key) used when iconKey is missing or unknown. */
  fallbackBranchKey?: string | null;
  /** Pixel size passed to the lucide component. Default: 16. */
  size?: number;
  /** Stroke colour. */
  color?: string;
  /** className passthrough — preferred way to size icons in flex/grid layouts. */
  className?: string;
  /** Stroke width passthrough. */
  strokeWidth?: number;
}

/**
 * Single source of truth for rendering ontology icons in the UI.
 *
 * Lookup order:
 *   1. Direct match on `iconKey`.
 *   2. Direct match on `fallbackBranchKey` (branches and objects share the
 *      same key namespace).
 *   3. `CircleHelp` (the generic unknown glyph).
 *
 * The legacy regex-based matcher (`objectIconComponent` in `branchIcons.tsx`)
 * remains exported for integrators that haven't migrated to icon_key yet, but
 * new code should pass an explicit `iconKey` and let this component resolve it.
 */
export function IconRenderer(props: IconRendererProps): JSX.Element {
  const { iconKey, fallbackBranchKey, size = 16, color, className, strokeWidth } = props;

  const direct = iconKey ? ICON_BY_KEY[iconKey] : undefined;
  if (direct) {
    const Comp = direct.Component;
    return <Comp size={size} color={color} className={className} strokeWidth={strokeWidth} />;
  }

  const fallback = fallbackBranchKey ? ICON_BY_KEY[fallbackBranchKey] : undefined;
  if (fallback) {
    const Comp = fallback.Component;
    return <Comp size={size} color={color} className={className} strokeWidth={strokeWidth} />;
  }

  return <CircleHelp size={size} color={color} className={className} strokeWidth={strokeWidth} />;
}
