# FMV right panel defaults to Clips (not Tracks) on a fresh visit

**Path:** [frontend/src/components/FmvPlayer.tsx](../../frontend/src/components/FmvPlayer.tsx#L315-L322)
**Lines touched:** ~10
**Depends on:** `crossNav.fmvClipId` from `Shell` cross-workspace nav

## Decision

`sideTab` initial state is now `'clips'` when the workspace mounts without an incoming clip selection, and `'tracks'` only when cross-navigation arrives with `crossNav.fmvClipId` (e.g. the analyst jumped here from the map after clicking an FMV detection).

```tsx
const [sideTab, setSideTab] = useState<SidePanelTab>(
  crossNav?.fmvClipId ? 'tracks' : 'clips',
);
```

## Why this design

The previous default was `'tracks'` unconditionally. On the Tracks tab with no `selectedId`, the panel renders the small text *"Select a clip in the Clips tab."* That copy is 11 px and easy to miss next to the empty video player on the left. Users who had just uploaded a clip via the Admin Ingest tab consistently reported "after uploading a drone video, nothing appears in the drone video page" — even though `GET /api/fmv/clips` was returning their clip and `fetchClips()` had populated the `clips` state. The clip was simply behind a sub-tab they didn't realise they needed to click.

Branching on `crossNav.fmvClipId` keeps the analysis-first behavior intact for the well-defined "map → FMV deep-link" flow, where Tracks is the obviously right landing tab because the clip is already implied.

## Considered alternatives

- **Hard-default to `'clips'` always.** Rejected: forces an extra click for the map→FMV deep-link case where Tracks is the correct destination.
- **Auto-switch to `'tracks'` the first time `selectedId` flips from null → number.** Rejected: extra state machine for a one-click problem; users who pick a clip from the library may still want to keep browsing the library, and the Tracks tab is one click away anyway.
- **Make the "Select a clip in the Clips tab." text bigger.** Rejected: treats a UX symptom instead of removing the misdirection.

## Cross-references

- [frontend/workspace-fmv-player.md](../frontend/workspace-fmv-player.md)
- [frontend/app-and-routing.md](../frontend/app-and-routing.md) — cross-nav shape
