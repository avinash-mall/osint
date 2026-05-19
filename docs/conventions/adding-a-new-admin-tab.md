# Recipe — Add a New Admin Tab

## When this applies

A new operator workflow that doesn't fit one of the existing ten Admin tabs (Ontology, Upload imagery, Processing, AI models, Health dashboard, Conf overrides, Prompt profiles, Version history, Health alerts, Auth · LDAP).

## Steps

1. **Create `frontend/src/components/admin/<Name>View.tsx`** following the pattern of [admin/AlertsView.tsx](../../frontend/src/components/admin/AlertsView.tsx):

   ```tsx
   import { ViewHeader } from './ViewHeader'

   export function NameView() {
     // fetch data via SWR / fetch directly
     return (
       <div>
         <ViewHeader title="Your Tab" onRefresh={refetch} />
         {/* ... */}
       </div>
     )
   }
   ```

   Use [admin/time.ts](../../frontend/src/components/admin/time.ts) for timestamp formatting.

2. **Register in [AdminScreen.tsx](../../frontend/src/components/AdminScreen.tsx):**

   ```tsx
   import { NameView } from './admin/NameView'

   const TABS = [
     // ...existing
     { id: 'name', label: 'Your Tab', component: NameView },
   ]
   ```

3. **API endpoints.** If you need new ones, follow [adding-a-new-router.md](adding-a-new-router.md).

4. **WebSocket updates.** If your tab needs live updates, subscribe via [useEventStream.ts](../../frontend/src/hooks/useEventStream.ts). Pick an existing topic or add a new one — document it in [operations/websocket-event-channels.md](../operations/websocket-event-channels.md).

5. **Visual test.** Add a Playwright test that opens the tab and asserts on a stable element. See [testing/playwright-frontend.md](../testing/playwright-frontend.md).

6. **Write a doc** at `docs/frontend/admin-<name>.md` and update [INDEX.txt](../INDEX.txt) + [workspace-admin.md](../frontend/workspace-admin.md).

## Conventions

- **One tab = one component file** under `admin/`.
- **No business logic in the tab file** beyond fetching and rendering. Validation lives in the backend (Pydantic) or in shared utils.
- **Polling intervals** typically 2-5 s. Use a longer interval (30 s+) for slow-changing data.
- **Empty state** must be intentional — show "No alerts" rather than a blank panel.

## Cross-references

- [frontend/workspace-admin.md](../frontend/workspace-admin.md)
- [frontend/admin-health-dashboard.md](../frontend/admin-health-dashboard.md) (canonical example)
- [adding-a-new-router.md](adding-a-new-router.md)
