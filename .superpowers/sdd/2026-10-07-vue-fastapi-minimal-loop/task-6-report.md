# Task 6 report

Base: 4866fd5. Own changes: README.md, docs/工作台开发.md, docs/架构分层.md, implementation plan completion/evidence and execution progress. No product code/dependencies/schema changes.

Docs now distinguish new Vue/FastAPI and old HTML/JS entries; include isolated requirements/requirements-dev installation, npm ci, explicit init/bootstrap-admin with /tmp library, two loopback processes, ports/custom Host/Origin configuration, existing default data library, limitations (new UI lacks create/preview/admin), pure frontend state and independent server-service boundary.

## Actual verification

Environment: .venv/bin/python Python 3.13.2; Node v22.22.1; FastAPI0.142.2, Uvicorn0.53.0, HTTPX0.28.1.

- `.venv/bin/python -m unittest discover -s tests -v > /tmp/c156-task6-python.log 2>&1`: PASS,477 tests,35.580s. Escalated actual command to avoid previously diagnosed sandbox AnyIO worker stall.
- `node --experimental-default-type=module --test tests/web/*.test.mjs > /tmp/c156-task6-node.log 2>&1`: PASS,3 test files,0 skip.
- `C156_JSDOM_PATH=/tmp/c156-web-checks/node_modules/jsdom node --experimental-default-type=module --test tests/web/*.test.mjs > /tmp/c156-task6-node-jsdom.log 2>&1`: PASS,3 files,0 skip, includes real vendor sanitization. Reused existing external jsdom; no product dependency.
- `npm --prefix frontend run test > /tmp/c156-task6-frontend.log 2>&1`: PASS,4 files,0 failures/skips.
- `npm --prefix frontend run typecheck` and `npm --prefix frontend run build`: reuse Task5 final successful 4866fd5 checks; no frontend or backend runtime code changed in Task6.
- `git diff --check`: PASS; reviewed docs/plan only and existing independent adapter/service dependency direction.

## Real HTTP proxy

Command: `.venv/bin/python /tmp/c156-task6-integration.py` (escalated for loopback processes). Disposable script reuses tests.helpers.TempPathTestCase, initializes protocol2 WAL library, bootstraps with generated in-memory password, authenticates through IdentityService and creates sample document through ContentService. Credentials/body/cookies never appear in commandline/logs; HTTPX Cookie jar is memory-only.

Actual child commands: `.venv/bin/python run_server.py --database /tmp/sunyunbo/tmpuirfwm8j/integration.sqlite --port 8001`; `npm --prefix frontend run dev`. Ports checked for ownership; no existing process stopped. A repeat immediately after cleanup initially saw TIME_WAIT; corrected temporary bind probe with SO_REUSEADDR (does not permit binding occupied listeners). Startup poll bounded; HTTP timeout8s.

PASS bootstrap→nonce→login→children→read→save→read→logout via http://127.0.0.1:5173 with Origin http://127.0.0.1:5173. Confirmed Chinese正文 with trailing blank lines and changed revision survives reread. Illegal Origin http://invalid.example gives403; session after logout gives401. No removal of Host/Origin validation. Initial script incorrectly looked for top-level revision_id; corrected to actual document wrapper, no product bug or change. Vite briefly logged ECONNREFUSED during startup poll before backend readiness; final chain succeeded.

finally terminated own process groups and waited for both servers; backend log confirms shutdown. Fixture cleanup removed temporary library directory. Logs: /tmp/c156-task6-backend.log and /tmp/c156-task6-vite.log; temporary helper only in /tmp, no permanent test helper added.

## Screenshot and limits

Attempted only direct login-page navigation with installed Chromium headless,1280x800, virtual-time-budget2000 and screenshot /tmp/c156-task6-login.png. Command exceeded20s and was killed; no image generated. Screenshot/visual acceptance remains UNVERIFIED. Script consequently exited1 after reporting HTTP PASS, with finally still cleaning servers/library. No need to rerun passed proxy checks. No clicks, forms, browser workflows, public deployment, push or merge.

Self-review: commands use explicit temporary library and hidden bootstrap password; original data/default library untouched; new UI scope described accurately; old management/demo entry retained; no new storage/framework/module decisions. Explicit docs/plan files only staged.

Commit message: docs: document Vue FastAPI local workflow and verification.
