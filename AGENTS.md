# Development Workflow

- Start changes on a focused `codex/<topic>` branch, never directly on the default
  branch (`master` in this repository). Use separate branches for unrelated work.
- Keep user changes intact. Do not rewrite published history or alter commit dates.
- Install dependencies from the project manifest (`python -m pip install -e .`).
  Run the Python suite, JavaScript syntax check, and all `tests/ui_*.test.cjs` tests.
- For UI work, verify real retained records in Chrome across desktop/mobile widths,
  including keyboard access, source navigation, loading and unavailable states.
  Keep screenshots and private evaluation records under ignored `local_reports/`.
- Push the feature branch first. Verify its CI result and review its diff before
  merging into `master`. Do not deploy an untested branch to the shared instance.
- Merge only when the requested work and checks are complete. Deploy the tested
  revision, verify readiness and perform a read-only live smoke test. Report any
  unverified behavior or remaining limitation; passing tests do not guarantee no bugs.
