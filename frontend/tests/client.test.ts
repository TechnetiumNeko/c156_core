import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiClient, ApiError } from '../src/api/client.ts';
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const grant = { user: { id: 'alice', login_name: 'alice', display_name: 'Alice', status: 'active', site_admin: false, version: 1 }, csrf: 'csrf', expires_at: 'date' };
test('public login request uses same origin and nonce; malformed content is rejected', async () => {
    const calls: [
        string,
        RequestInit | undefined
    ][] = [];
    const client = new ApiClient(async (url, init) => {
        calls.push([String(url), init]);
        return calls.length === 1 ? json({ initialized: true, nonce: 'nonce' }) : json(grant);
    });
    await client.bootstrap();
    await client.login('alice', 'password');
    assert.equal(calls[1][0], '/api/auth/login');
    assert.equal(calls[1][1]?.credentials, 'same-origin');
    assert.equal(new Headers(calls[1][1]?.headers).get('X-C156-Nonce'), 'nonce');
    assert.deepEqual(JSON.parse(String(calls[1][1]?.body)), { login_name: 'alice', password: 'password' });
    const malformed = new ApiClient(async () => json({ document: {} }));
    await assert.rejects(malformed.readDocument('x'), (error: ApiError) => error.code === 'response');
});
test('save and logout send csrf, exact bodies and public paths', async () => {
    const calls: [
        string,
        RequestInit | undefined
    ][] = [];
    const client = new ApiClient(async (url, init) => {
        calls.push([String(url), init]);
        return json({ ok: true });
    });
    client.csrf = 'proof';
    const body = { object_id: 'doc', content: 'text\n\n', expected_revision_id: 'r1' };
    await assert.rejects(client.saveDocument({ ...body, epoch: 9 } as typeof body));
    assert.equal(calls[0][0], '/api/document');
    assert.equal(calls[0][1]?.method, 'PUT');
    assert.deepEqual(JSON.parse(String(calls[0][1]?.body)), body);
    assert.equal(new Headers(calls[0][1]?.headers).get('X-C156-CSRF'), 'proof');
    await client.logout();
    assert.equal(calls[1][0], '/api/auth/logout');
    assert.equal(client.csrf, null);
});
test('late successful bootstrap and late 401 cannot alter new proofs', async () => {
    for (const status of [200, 401]) {
        let resolve!: (value: Response) => void;
        const client = new ApiClient(() => new Promise(r => {
            resolve = r;
        }));
        const pending = client.bootstrap();
        client.invalidate();
        client.csrf = 'new';
        resolve(json(status === 200 ? { initialized: true, nonce: 'old' } : { error: { code: 'unauthenticated', message: 'Expired' } }, status));
        await assert.rejects(pending, (error: ApiError) => error.code === 'stale');
        assert.equal(client.csrf, 'new');
        assert.equal(client.nonce, null);
    }
});
test('domain conflict and transport failure remain distinguishable', async () => {
    const conflict = new ApiClient(async () => json({ error: { code: 'conflict', message: 'Changed' } }, 409));
    await assert.rejects(conflict.readDocument('doc'), (error: ApiError) => error.code === 'conflict' && error.status === 409);
    const network = new ApiClient(async () => {
        throw Error('offline');
    });
    await assert.rejects(network.bootstrap(), (error: ApiError) => error.code === 'network');
});

test('authenticated nonmember bootstrap accepts null role for public root or no root, rejects invalid roles', async () => {
    const root = { id: 'root', kind: 'folder', name: 'Root', parent_id: null, position: 0, version: 1, path: '/', created_at: '', modified_at: '', metadata: {} };
    const access = { version: 1, actions: ['read'], visibility: 'public', frozen: false, can_freeze: false, can_unfreeze: false };
    for (const visible of [true, false]) {
        const value = { ...grant, initialized: true, scope: { workspace_id: 'workspace', branch_id: 'main' }, workspace_access_version: 1, workspace_role: null, root: visible ? root : null, root_access: visible ? access : null };
        const client = new ApiClient(async () => json(value));
        assert.deepEqual(await client.bootstrap(), value);
        assert.equal(client.csrf, 'csrf');
        for (const role of [42, {}, undefined]) {
            const invalid = new ApiClient(async () => json({ ...value, workspace_role: role }));
            await assert.rejects(invalid.bootstrap(), (error: ApiError) => error.code === 'response');
        }
    }
});

const receipt = { operation_id: 'op', operation_type: 'save', result_revision_id: 'r2', changed: true, created_at: '2026-10-01T00:00:00+00:00' };
const result = { operation: receipt, current_revision_id: 'r3' };
const revision = { revision_id: 'r1', parent_revision_id: null, actor_id: null, actor_display_name: null, source_kind: 'unknown', restored_from_revision_id: null, created_at: '2026-10-01T00:00:00+00:00' };

test('operation requests project fixed fields, preserve raw body and return receipt without a Document', async () => {
    const calls: [string, RequestInit | undefined][] = [];
    const client = new ApiClient(async (url, init) => { calls.push([String(url), init]); return json(result); });
    client.csrf = 'proof';
    const save = { object_id: 'doc', content: '正文\r\n\n', expected_revision_id: 'r1', operation_id: 'op' };
    assert.deepEqual(await client.saveDocumentOperation({ ...save, extra: 'ignored' } as typeof save), result);
    assert.deepEqual(JSON.parse(String(calls[0][1]?.body)), save);
    assert.equal(calls[0][1]?.method, 'PUT');
    assert.equal(new Headers(calls[0][1]?.headers).get('X-C156-CSRF'), 'proof');
    const restore = { object_id: 'doc', source_revision_id: 'r1', expected_revision_id: 'r2', operation_id: 'restore' };
    await client.restoreDocument(restore);
    assert.equal(calls[1][0], '/api/document/restore');
    assert.equal(calls[1][1]?.method, 'POST');
    assert.deepEqual(JSON.parse(String(calls[1][1]?.body)), restore);
    await client.getOperationStatus('doc /', 'op &');
    assert.equal(calls[2][0], '/api/document/operation?object_id=doc%20%2F&operation_id=op%20%26');
    const absent = new ApiClient(async () => json({operation: null}));
    assert.deepEqual(await absent.getOperationStatus('doc', 'op'), {operation: null});
});

test('operation malformed or missing fields and transport failures preserve an uncertain outcome', async () => {
    for (const value of [ {operation: null}, {operation: receipt}, {...result, document: {}},
        {...result, current_revision_id: ''}, ...Object.keys(receipt).map(key => ({...result, operation: Object.fromEntries(Object.entries(receipt).filter(([k]) => k !== key))})),
        {...result, operation: {...receipt, changed: 1}}, {...result, operation: {...receipt, operation_type: 'delete'}},
        {...result, operation: {...receipt, created_at: 'yesterday'}}, {...result, operation: {...receipt, content: 'leak'}} ]) {
        const client = new ApiClient(async () => json(value)); client.csrf = 'proof';
        await assert.rejects(client.saveDocumentOperation({object_id: 'doc', content: '', expected_revision_id: 'r1', operation_id: 'op'}),
            (error: ApiError) => error.code === 'response' && error.outcome === 'uncertain');
    }
    for (const response of [() => Promise.reject(Error('offline')), async () => new Response('{'),
        async () => json({error: {code: 'internal_error', message: 'Failed'}}, 500)]) {
        const client = new ApiClient(response);
        await assert.rejects(client.getOperationStatus('doc', 'op'), (error: ApiError) => error.outcome === 'uncertain');
    }
    for (const [code, status] of [['conflict', 409], ['frozen', 403], ['storage_busy', 503]] as const) {
        const client = new ApiClient(async () => json({error: {code, message: 'Rejected'}}, status));
        await assert.rejects(client.getOperationStatus('doc', 'op'), (error: ApiError) => error.code === code && error.outcome === 'rejected');
    }
});

test('history APIs encode query parameters and validate each public projection', async () => {
    const cases: [string, unknown, (client: ApiClient) => Promise<unknown>][] = [
        ['/api/document/history?object_id=d%20%2F&cursor=c%26&limit=2', {revisions: [revision], head_revision_id: 'r1', next_cursor: null}, c => c.listDocumentRevisions('d /', 'c&', 2)],
        ['/api/document/revision?object_id=d&revision_id=r%26', {...revision, content: ''}, c => c.readDocumentRevision('d', 'r&')],
        ['/api/document/diff?object_id=d&from_revision_id=r1&to_revision_id=r2', {from_revision_id: 'r1', to_revision_id: 'r2', diff: ''}, c => c.compareDocumentRevisions('d', 'r1', 'r2')],
        ['/api/documents/deleted?cursor=c%26&limit=2', {documents: [{object_id: 'd', name: 'name', path: '/name'}], next_cursor: null}, c => c.listDeletedDocuments('c&', 2)],
    ];
    for (const [path, value, invoke] of cases) {
        const client = new ApiClient(async url => {assert.equal(String(url), path); return json(value);});
        assert.deepEqual(await invoke(client), value);
        for (const key of Object.keys(value as object)) {
            const incomplete = Object.fromEntries(Object.entries(value as object).filter(([k]) => k !== key));
            await assert.rejects(invoke(new ApiClient(async () => json(incomplete))), (error: ApiError) => error.code === 'response');
        }
    }
    for (const invalid of [{...revision, source_kind: 'invalid'}, {...revision, source_kind: ['save']}, {...revision, source_kind: {toString: 'save'}}, {...revision, actor_id: 4}, {...revision, content: 'body'}]) {
        const client = new ApiClient(async () => json({revisions: [invalid], head_revision_id: 'r1', next_cursor: null}));
        await assert.rejects(client.listDocumentRevisions('d'), (error: ApiError) => error instanceof ApiError && error.code === 'response' && error.outcome === 'uncertain');
    }
});

test('authenticated bootstrap requires complete scope without inferring it from root', async () => {
    const value = {...grant, initialized: true, workspace_access_version: 1, workspace_role: null, root: null, root_access: null};
    for (const scope of [undefined, {}, {workspace_id: 'w'}, {workspace_id: 'w', branch_id: 1}]) {
        await assert.rejects(new ApiClient(async () => json({...value, scope})).bootstrap(), (error: ApiError) => error.code === 'response');
    }
    assert.deepEqual(await new ApiClient(async () => json({...value, scope: {workspace_id: 'w', branch_id: 'b'}})).bootstrap(),
        {...value, scope: {workspace_id: 'w', branch_id: 'b'}});
});
