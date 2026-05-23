# Manual-draw modal replaces native window.prompt

## What changed

Drawing a manual bounding box used to call `window.prompt()` to ask the operator for an object-class label. That call has been replaced with a themed in-app dialog component, `ManualDetectionDialog.tsx`, mounted alongside `MapStage`.

## Why

Hardened defense browser profiles (Chrome Enterprise, locked-down JOC builds) block or auto-dismiss native `prompt()` dialogs, which silently locked operators out of adding manual targets. The new dialog:

- Is a normal DOM element — never blocked by browser policy.
- Matches the dark workstation theme.
- Is keyboard-accessible (auto-focus, ESC cancel, backdrop click cancel).

## Implementation

- New file: [frontend/src/components/map/ManualDetectionDialog.tsx](../../frontend/src/components/map/ManualDetectionDialog.tsx). Pattern mirrors [ChangeDetectionDialog.tsx](../../frontend/src/components/map/ChangeDetectionDialog.tsx).
- `MapStage` now keeps a `stagedManualBounds` state; `DrawRectHandler.onFinish` stores the bounds and the dialog renders against them. Confirmation calls `createManualDetection(bounds, { object_class })`.

## Cross-references

- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
