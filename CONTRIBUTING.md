# Contributing

PaperCreator is an experimental, low-priority project. Issues and pull requests are welcome, but maintainers do not guarantee response times, acceptance, compatibility, or a release schedule.

## Before changing code

1. Read `README.md`, `docs/llm_context.md`, and the relevant system document under `docs/systems/`.
2. Do not commit `.env`, API keys, workbench data, databases, unpublished papers, model caches, logs, or generated installers.
3. Keep changes scoped. State whether a change affects the database, project files, network requests, privacy, cost, or destructive operations.
4. Do not describe AI output, heuristic gaps, or automated quality checks as factual research conclusions.

## Validation

Run checks in proportion to the change. The normal baseline is:

```powershell
npm run test:backend
npm run typecheck
npm run build
npm run validate:docs
```

Desktop workflow changes should also run `npm run test:e2e`. Packaging changes require the release and installer checks described in `docs/testing_guide.md`.

In a pull request, list commands actually run and identify external services, hardware, credentials, or manual review that were not available.
