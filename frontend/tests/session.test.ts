import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiClient, ApiError } from '../src/api/client.ts';
import { EditorState } from '../src/state/editor.ts';
import { SessionState } from '../src/state/session.ts';
import type { Document } from '../src/api/types.ts';
const node = { id: 'root', kind: 'folder', name: 'Root', parent_id: null, position: 0, version: 1, path: '/', created_at: '', modified_at: '', metadata: {} };
const access = { version: 1, actions: ['read'], visibility: 'visible', frozen: false, can_freeze: false, can_unfreeze: false };
const grant = (id: string) => ({ user: { id, login_name: id, display_name: id, status: 'active', site_admin: false, version: 1 }, csrf: id + '-csrf', expires_at: 'date' });
const bootstrap = (id: string) => ({ initialized: true, ...grant(id), scope: { workspace_id: 'workspace', branch_id: 'main' }, workspace_access_version: 1, workspace_role: 'owner', root: { ...node, id: id + '-root' }, root_access: access });
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const doc: Document = { ...node, id: 'doc', kind: 'document', content: 'original', revision_id: 'r1' };
test('login refreshes root, blocks foreign draft, requires explicit discard', async () => {
    const calls: string[] = [];
    const client = new ApiClient(async (url) => {
        calls.push(String(url));
        return json(String(url).includes('/login') ? grant('bob') : bootstrap('bob'));
    });
    client.nonce = 'nonce';
    const editor = new EditorState();
    editor.setIdentity('alice');
    editor.open(doc);
    editor.edit('alice draft');
    const session = new SessionState(client, editor);
    assert.equal(await session.login('bob', 'pw'), false);
    assert.deepEqual(calls, ['/api/auth/login', '/api/bootstrap']);
    assert.equal(session.root, null);
    assert.equal(session.user, null);
    assert.equal(session.blocked, true);
    assert.equal(editor.draft, 'alice draft');
    assert.equal(await session.acceptPending(), false);
    assert.equal(await session.acceptPending({ discard: true }), true);
    assert.equal((session.root as typeof node | null)?.id, 'bob-root');
    assert.equal(editor.document, null);
});
test('expiry refreshes anonymous nonce after invalid-cookie 401; same user resumes draft', async () => {
    let call = 0;
    const client = new ApiClient(async () => {
        call++;
        if (call === 1)
            return json({ error: { code: 'unauthenticated', message: 'Expired' } }, 401);
        if (call === 2)
            return json({ initialized: true, nonce: 'fresh' });
        if (call === 3)
            return json(grant('alice'));
        return json(bootstrap('alice'));
    });
    const editor = new EditorState();
    editor.setIdentity('alice');
    editor.open(doc);
    editor.edit('mine');
    const session = new SessionState(client, editor);
    await session.expire();
    assert.equal(client.nonce, 'fresh');
    assert.equal(editor.paused, true);
    assert.equal(editor.draft, 'mine');
    assert.equal(await session.login('alice', 'pw'), true);
    assert.equal(editor.paused, false);
    assert.equal(editor.draft, 'mine');
    assert.equal(session.root?.id, 'alice-root');
});
test('late session bootstrap success or 401 cannot replace current anonymous session', async () => {
    for (const status of [200, 401]) {
        let resolve!: (response: Response) => void;
        let count = 0;
        const client = new ApiClient(() => {
            if (++count === 1)
                return new Promise(r => {
                    resolve = r;
                });
            return Promise.resolve(json({ initialized: true, nonce: 'current' }));
        });
        const session = new SessionState(client, new EditorState());
        const old = session.bootstrap();
        await session.expire();
        resolve(json(status === 200 ? bootstrap('old') : { error: { code: 'unauthenticated', message: 'Expired' } }, status));
        await assert.rejects(old, (error: ApiError) => error.code === 'stale');
        assert.equal(session.user, null);
        assert.equal(session.root, null);
        assert.equal(client.nonce, 'current');
    }
});
test('logout obtains anonymous nonce before next login', async () => {
    const calls: string[] = [];
    const client = new ApiClient(async (url) => {
        calls.push(String(url));
        return json(String(url).includes('logout') ? { ok: true } : { initialized: true, nonce: 'new' });
    });
    client.csrf = 'proof';
    const session = new SessionState(client, new EditorState());
    assert.equal(await session.logout(), true);
    assert.deepEqual(calls, ['/api/auth/logout', '/api/bootstrap']);
    assert.equal(client.nonce, 'new');
});
test('wrong password preserves draft and refreshes nonce for successful retry', async () => {
    const requests: [
        string,
        RequestInit | undefined
    ][] = [];
    const client = new ApiClient(async (url, init) => {
        requests.push([String(url), init]);
        if (requests.length === 1)
            return json({ error: { code: 'unauthenticated', message: 'Wrong password' } }, 401);
        if (requests.length === 2)
            return json({ initialized: true, nonce: 'recovered' });
        if (requests.length === 3)
            return json(grant('alice'));
        return json(bootstrap('alice'));
    });
    client.nonce = 'initial';
    const editor = new EditorState();
    editor.setIdentity('alice');
    editor.open(doc);
    editor.edit('protected draft');
    const session = new SessionState(client, editor);
    await assert.rejects(session.login('alice', 'wrong'), (error: ApiError) => error.status === 401 && error.message === 'Wrong password');
    assert.equal(client.nonce, 'recovered');
    assert.equal(editor.draft, 'protected draft');
    assert.equal(editor.owner, 'alice');
    assert.equal(await session.login('alice', 'correct'), true);
    assert.equal(new Headers(requests[2][1]?.headers).get('X-C156-Nonce'), 'recovered');
    assert.equal(session.user?.id, 'alice');
    assert.equal(editor.draft, 'protected draft');
});
test('obsolete authentication recovery cannot overwrite newer anonymous nonce', async () => {
    let recover!: (value: Response) => void;
    let count = 0;
    const client = new ApiClient(async () => {
        if (++count === 1)
            return json({ error: { code: 'unauthenticated', message: 'Wrong password' } }, 401);
        if (count === 2)
            return new Promise(resolve => {
                recover = resolve;
            });
        return json({ initialized: true, nonce: 'current' });
    });
    client.nonce = 'initial';
    const session = new SessionState(client, new EditorState());
    const obsolete = session.login('alice', 'wrong');
    while (!recover)
        await new Promise(resolve => setImmediate(resolve));
    await session.expire();
    recover(json({ initialized: true, nonce: 'obsolete' }));
    assert.equal(await obsolete, false);
    assert.equal(client.nonce, 'current');
    assert.equal(session.user, null);
});

test('successful login with failed bootstrap can recover same-user draft through bootstrap retry', async () => {
    let call = 0;
    const client = new ApiClient(async () => {
        if (++call === 1) return json(grant('alice'));
        if (call === 2) throw Error('offline');
        return json(bootstrap('alice'));
    });
    client.nonce = 'initial';
    const editor = new EditorState();
    editor.setIdentity('alice'); editor.open(doc); editor.edit('kept');
    const session = new SessionState(client, editor);
    await assert.rejects(session.login('alice', 'pw'), (error: ApiError) => error.code === 'network');
    assert.equal(session.recoveryNeeded, true);
    assert.equal(session.user, null); assert.equal(client.nonce, null);
    assert.equal(editor.paused, true); assert.equal(editor.draft, 'kept');
    assert.equal(await session.bootstrap(), true);
    assert.equal(session.recoveryNeeded, false); assert.equal((session.user as { id: string } | null)?.id, 'alice');
    assert.equal(editor.paused, false); assert.equal(editor.draft, 'kept');
});
test('failed anonymous proof recovery after wrong password or logout exposes recoverable state', async () => {
    for (const operation of ['login', 'logout'] as const) {
        let call = 0;
        const client = new ApiClient(async () => {
            if (++call === 1) return operation === 'login'
                ? json({ error: { code: 'unauthenticated', message: 'Wrong password' } }, 401)
                : json({ ok: true });
            if (call === 2) throw Error('offline');
            if (call === 3) return json({ initialized: true, nonce: 'recovered' });
            if (call === 4) return json(grant('alice'));
            return json(bootstrap('alice'));
        });
        client.nonce = 'initial'; client.csrf = 'proof';
        const editor = new EditorState();
        editor.setIdentity('alice'); editor.open(doc); editor.edit('kept');
        const session = new SessionState(client, editor);
        await assert.rejects(operation === 'login' ? session.login('alice', 'wrong') : session.logout());
        assert.equal(session.recoveryNeeded, true); assert.equal(editor.draft, operation === 'logout' ? '' : 'kept');
        assert.equal(editor.paused, true);
        await session.bootstrap();
        assert.equal(session.recoveryNeeded, false); assert.equal(client.nonce, 'recovered');
        await session.login('alice', 'correct');
        assert.equal(session.user?.id, 'alice'); assert.equal(editor.draft, operation === 'logout' ? '' : 'kept');
    }
});

test('bootstrap retry after successful foreign login keeps old draft blocked until discard', async () => {
    let call = 0;
    const client = new ApiClient(async () => {
        if (++call === 1) return json(grant('bob'));
        if (call === 2) throw Error('offline');
        return json(bootstrap('bob'));
    });
    client.nonce = 'initial';
    const editor = new EditorState();
    editor.setIdentity('alice'); editor.open(doc); editor.edit('alice draft');
    const session = new SessionState(client, editor);
    await assert.rejects(session.login('bob', 'pw'));
    assert.equal(await session.bootstrap(), false);
    assert.equal(session.recoveryNeeded, false); assert.equal(session.blocked, true);
    assert.equal(editor.draft, 'alice draft'); assert.equal(editor.owner, 'alice');
    assert.equal(editor.paused, true); assert.equal(session.user, null);
    assert.equal(await session.acceptPending({ discard: true }), true);
    assert.equal((session.user as { id: string } | null)?.id, 'bob'); assert.equal(editor.document, null);
});


test('logout lifecycle drains before hiding content and authenticating logout; failure retains memory', async () => {
    let finish!: () => void;
    let fail = false;
    const calls: string[] = [];
    const client = new ApiClient(async url => { calls.push(String(url)); return json(String(url).includes('logout') ? { ok: true } : { initialized: true, nonce: 'fresh' }); });
    client.csrf = 'proof';
    const editor = new EditorState(); editor.setIdentity('alice'); editor.open(doc); editor.edit('kept');
    const session = new SessionState(client, editor, { async beforeLeaveIdentity() { if (fail) throw new Error('disk full'); await new Promise<void>(resolve => { finish = resolve; }); } });
    fail = true; await assert.rejects(session.logout()); assert.equal(editor.draft, 'kept'); assert.deepEqual(calls, []);
    fail = false; const logout = session.logout();
    assert.equal(editor.draft, 'kept'); assert.deepEqual(calls, []);
    finish(); await logout; assert.equal(editor.document, null); assert.equal(editor.draft, ''); assert.deepEqual(calls, ['/api/auth/logout', '/api/bootstrap']);
});
