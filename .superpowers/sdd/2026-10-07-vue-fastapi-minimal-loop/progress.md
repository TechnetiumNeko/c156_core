# SDD ledger — plan: docs/superpowers/plans/2026-10-07-vue-fastapi-minimal-loop.md

Workspace: .worktrees/vue-fastapi-loop; branch feat/vue-fastapi-loop; base cc1e8a7.
User authorized plan review and subagent execution. No implementation tasks completed yet.

## Preflight review

| Tasks | Shared interface/files | Finding |
| --- | --- | --- |
| 1 | config, startup, tests | consistent; custom port default allowlist clarified in plan |
| 2 | auth/transport/api | consistent; async reads vs sync service calls explicit |
| 3 | service calls and API tests | consistent, uses public services |
| 4 | API types/editor/session | login/expiry bootstrap sequencing clarified |
| 5 | Vue/reactive state | ticket equality must withstand Vue proxies |
| 6 | smoke/docs/checks | no browser clicks, temporary DB only |
| 1/2 | app.state.services, app.py | sequential composition |
| 1/3 | services.scope/content | public service calls, no SQL |
| 1/4 | allowlists and Vite config | host preserved; matching loopback defaults |
| 1/6 | CLI and startup docs | custom port defaults clarified |
| 2/3 | routes/schema/serialization/api tests | same error and auth boundary |
| 2/4 | bootstrap/session/nonce/csrf | login bootstraps root, expiry fetches new nonce |
| 2/5 | errors/access/identity | UI binds current epoch; no client authorization |
| 2/6 | transport/proxy | actual HTTP smoke required |
| 3/4 | document/node/access response | types follow actual JSON |
| 3/5 | save response/access | retain draft, readonly from capabilities |
| 3/6 | persistence | smoke through real Vite proxy |
| 4/5 | client/editor/session | isolated state retained across panel switches |
| 4/6 | npm scripts | node strip-types, vue-tsc, vite build |
| 5/6 | minimal UI/screenshot | no interactions, no permanent harness |
| 1/5 | no direct shared files | Vue calls API only |

Ruling: CLI default Host/Origin follows actual backend port; explicit lists override — fixed-port defaults would reject valid custom-port launches — if wrong, configuration behavior needs rework.
Ruling: login bootstraps current root; expiry/logout obtains anonymous nonce before relogin — prevents stale identity/root/nonce in state — if wrong, session transitions need rework.

## Tasks

- Task 1: pending; base cc1e8a7.
- Task 2: pending.
- Task 3: pending.
- Task 4: pending.
- Task 5: pending.
- Task 6: pending.

Task 1: implementation b39e6f5, 9 startup/architecture tests pass; task reviewer dispatched.

Task 1: complete (commits cc1e8a7..b39e6f5, review clean). Cross-diff default path matches previously inspected src/web/app.py; primary package metadata evidence retained in task report.
Task 2: starting; base b39e6f5.

Task 2: implementation 51bee6a; 9 transport/API tests pass in escalated run. Sandbox AnyIO stall diagnosed, no test relaxation. Review dispatched.

Task 2: fix round 1/5 requested. Important: Starlette generic handler rethrows to Uvicorn, risking sensitive traceback logging. Catch unexpected exceptions before outer middleware and test nonpropagation.

Task 2: fix round 1/5 (1 addressed, 0 open; commit 46088b0).
Task 2: complete (commits b39e6f5..46088b0, review clean).
Task 3: starting; base 46088b0.

Task 3: complete (commits 46088b0..bc4fea4, review clean). 29 API/access/concurrency tests pass.
Task 4: starting; base bc4fea4.

Task 4: implementation c0e96eb; Node production state tests and vue-tsc pass, review dispatched. Report notes acceptPending({discard:true}) interface for Task 5.

Task 4: fix round 1/5 requested. Important: login 401 clears nonce without automatic anonymous proof recovery. Minor readability assigned same implementer for affected state/tests.

Task 4: fix round 1/5 (login recovery addressed, 0 open; commit 40bba6e); readability minor addressed.
Task 4: complete (commits bc4fea4..40bba6e, review clean).
Task 5: starting; base 40bba6e.

Task 5: fix round 1/5 requested. Important: failed relogin capability read leaves retained draft read-only with no retry UI. Preserve draft/base while retrying access. Browser acceptance unverified per scope, Task6 screenshot/HTTP smoke covers limited evidence.

Task 5: fix round 1/5 (original retry finding addressed; new 1 open). New conflict merge action enabled during access refresh invalidates response and retry state. Fix round 2/5 requested.

Task 5: fix round 2/5 (1 addressed, 0 open; commit 4866fd5).
Task 5: complete (commits 40bba6e..4866fd5, review clean).
Task 6: starting; base 4866fd5.

Task 6: implementation and planned regression complete; real Vite/Uvicorn proxy PASS; optional login screenshot timed out and remains unverified. Report: task-6-report.md. Awaiting independent review.
