# `services/analytics.ts` — Analytics API Client

**Path:** [frontend/src/services/analytics.ts](../../frontend/src/services/analytics.ts)
**Lines:** ~1693 characters

## Purpose

Thin typed wrapper around `/api/analytics/*` endpoints. Exposes a small set of TypeScript types (`LatLon`, `AnalyticsMode`, `AnalyticsJob`) used by [map-analytics-tools.md](map-analytics-tools.md).

## Why one file

There's only one services module today because analytics is the only feature with enough boilerplate to extract. Other endpoints are called inline from their owning component. As the surface grows, more services modules will likely appear — but **don't add a service module just for type aliasing**; only do it when the component file is being dragged down by API plumbing.

## Cross-references

- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md)
- [map-analytics-tools.md](map-analytics-tools.md)
