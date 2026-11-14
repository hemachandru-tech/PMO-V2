## What and why

<!-- Backlog items: US-###, DEC-##. Link to the FEAT / REQ / RULE ids this implements. -->

## Definition of Done (task.md)

- [ ] CI green (lint, types, unit, security suites)
- [ ] Acceptance tests named in the story are automated and passing
- [ ] Scope and read-only guarantees untouched or re-verified (scope isolation suite passes)
- [ ] No secrets, prompt text, result rows or SQL literals in logs
- [ ] OpenAPI / generated TypeScript types updated if the API changed
- [ ] Audit events emitted for new governance or security actions
- [ ] Migrations upgrade and downgrade cleanly (expand → contract)
- [ ] Docs updated, story ticked in task.md, entry added to implement_log.md
