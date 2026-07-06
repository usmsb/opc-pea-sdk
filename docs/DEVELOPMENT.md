# PEA SDK Development

## Source Layout

- `src/pea_core`: reusable Python runtime copied from `peas/pea_core`.
- `examples/*`: mirrored README files for existing PEA cases.
- `templates/PEA_TEMPLATE_MATRIX.md`: classification and build flow.

## What Belongs In The SDK

- Shared Harness mechanics.
- Context assembly, rolling summaries and RAG memory.
- Admin login, entity specs, generic CRUD and upload handling.
- Embedding provider abstractions and sandbox fallbacks.
- Static admin assets that are not business-specific.

## What Stays In Each PEA

- Industry tools, prompts, product catalogs and pricing.
- Payment, Mini Program, provider and operator secrets.
- SQLite data, media uploads and browser profiles.
- Deployment manifests for the concrete PEA service.

## Release Checklist

1. Make changes in `usmsb/opc-platform`.
2. Run `python scripts/sync_pea_sdk_repo.py --target /tmp/opc-pea-sdk --clean`.
3. Run `python -m compileall /tmp/opc-pea-sdk/src/pea_core`.
4. Commit and push OPC platform.
5. Let the sync workflow push the standalone SDK repository.
