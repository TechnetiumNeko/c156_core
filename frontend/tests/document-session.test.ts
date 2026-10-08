import test from 'node:test';
import assert from 'node:assert/strict';
import { reactive } from 'vue';
import { IDBFactory } from 'fake-indexeddb';
import { ApiClient, ApiError } from '../src/api/client.ts';
import { DraftStore, StaleDraftFenceError } from '../src/drafts/store.ts';
import { acquireDraftLease } from '../src/drafts/lease.ts';
import { draftDTO, type DraftKey, type DraftRecord, type DraftStorage, type PendingOperation } from '../src/drafts/types.ts';
import { DocumentSession } from '../src/state/document-session.ts';
import { EditorState } from '../src/state/editor.ts';
import { SessionState } from '../src/state/session.ts';
const key: DraftKey = { userId: 'alice', workspaceId: 'work', branchId: 'main', objectId: 'doc' };
const scope = { workspace_id: 'work', branch_id: 'main' };
const node = { id: 'doc', kind: 'document', name: 'Doc', parent_id: null, position: 0, version: 1, path: '/', created_at: '', modified_at: '', metadata: {} };
const doc = (content = 'original', revision_id = 'r1') => ({ ...node, content, revision_id });
const access = { version: 1, actions: ['read', 'write'], visibility: 'visible', frozen: false, can_freeze: false, can_unfreeze: false };
const user = (id = 'alice') => ({ id, login_name: id, display_name: id, status: 'active', site_admin: false, version: 1 });
const auth = (id = 'alice') => ({ user: user(id), csrf: 'csrf', expires_at: 'date' });
const bootstrap = (id = 'alice') => ({ ...auth(id), initialized: true, scope, workspace_access_version: 1, workspace_role: 'owner', root: node, root_access: access });
const receipt = (id: string, head = 'r2', kind: 'save' | 'restore' = 'save') => ({ operation: { operation_id: id, operation_type: kind, result_revision_id: 'r2', changed: true, created_at: '2026-01-01T00:00:00Z' }, current_revision_id: head });
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
class ControlledStore implements DraftStorage {
    real = new DraftStore(new IDBFactory(), 'test');
    gate: Promise<void> | null = null;
    failure = false;
    writes = 0;
    claim(k: DraftKey) { return this.real.claim(k); }
    read(k: DraftKey) { return this.real.read(k); }
    clear(k: DraftKey, token: number) { return this.real.clear(k, token); }
    async write(r: DraftRecord, token: number) { this.writes++; await this.gate; if (this.failure) throw new Error('disk full'); await this.real.write(r, token); }
    flush() { return this.real.flush(); }
}
function setup() {
    const store = new ControlledStore(); const editor = reactive(new EditorState()) as EditorState;
    const writes: Record<string, string>[] = []; let head = doc(); let identity = 'alice'; let denied = false; let loggedOut = false;
    let mutation: (request: Record<string, string>) => Promise<Response> = async request => { head = doc(request.content ?? 'restored', 'r2'); return json(receipt(request.operation_id, 'r2', request.source_revision_id ? 'restore' : 'save')); };
    let status: () => Promise<Response> = async () => json({ operation: null });
    let historyDenied = false;
    const client = new ApiClient(async (url, init) => {
        const path = String(url);
        if (path === '/api/bootstrap') return json(loggedOut ? { initialized: true, nonce: 'anonymous' } : bootstrap(identity));
        if (path === '/api/auth/logout') { loggedOut = true; return json({ ok: true }); }
        if (path === '/api/session') return json(auth(identity));
        if (path.includes('/operation?')) return status();
        if (path.includes('/revision?')) return historyDenied ? json({ error: { code: 'forbidden', message: 'denied' } }, 403) : json({ revision_id: 'r1', parent_revision_id: null, actor_id: null, actor_display_name: null, source_kind: 'save', restored_from_revision_id: null, created_at: '2026-01-01T00:00:00Z', content: 'original' });
        if (init?.method === 'PUT' || init?.method === 'POST') { const request = JSON.parse(String(init.body)); writes.push(request); return mutation(request); }
        return denied ? json({ error: { code: 'forbidden', message: 'denied' } }, 403) : json({ document: head, access });
    });
    let releases = 0; let ids = 0;
    const session = reactive(new DocumentSession(client, editor, store, { acquireLease: async () => ({ async release() { releases++; } }), operationId: () => 'op-' + ++ids }));
    return { client, store, editor, session, writes, setMutation: (fn: typeof mutation) => { mutation = fn; }, setStatus: (fn: typeof status) => { status = fn; }, setHead: (d: typeof head) => { head = d; }, setIdentity: (id: string) => { identity = id; loggedOut = false; }, pause: () => { loggedOut = true; }, deny: () => { denied = true; }, denyHistory: () => { historyDenied = true; }, releases: () => releases };
}
async function seed(store: DraftStorage, pending = false) {
    const record = await store.claim(key);
    await store.write({ ...record, generation: 1, content: 'saved local', baseRevisionId: 'r1', pending: pending ? { kind: 'save', request: { operation_id: 'old-op', object_id: 'doc', expected_revision_id: 'r1', content: 'sent' } } : null }, record.fencingToken);
}
test('production IndexedDB adapter projects Vue DTOs and fences same-owner clear and takeover', async () => {
    const store = new DraftStore(new IDBFactory(), 'fences');
    const first = await store.claim(key);
    const record = reactive({ ...first, content: 'local', generation: 2, pending: { kind: 'save' as const, request: { operation_id: 'id', object_id: 'doc', expected_revision_id: 'r1', content: 'local' } } });
    await store.write(record, first.fencingToken);
    assert.equal((await store.read(key))?.pending?.request.operation_id, 'id');
    // An acknowledgement keeps this owner/token but advances generation past old captured pending writes.
    await store.write({ ...record, generation: 3, content: 'later input', pending: null }, first.fencingToken);
    await assert.rejects(store.write(record, first.fencingToken), StaleDraftFenceError);
    assert.equal((await store.read(key))?.pending, null);
    assert.equal((await store.read(key))?.content, 'later input');
    const cleared = await store.clear(key, first.fencingToken);
    assert.equal(cleared.content, null);
    await assert.rejects(store.write(record, first.fencingToken), StaleDraftFenceError);
    await assert.rejects(store.clear(key, first.fencingToken), StaleDraftFenceError);
    const taken = await store.claim(key);
    await assert.rejects(store.write({ ...cleared, content: 'revived' }, cleared.fencingToken), StaleDraftFenceError);
    assert.equal((await store.read(key))?.fencingToken, taken.fencingToken);
    assert.equal((await store.read(key))?.content, null);
    await store.flush();
});
test('production IndexedDB write resolves after transaction completion and abort does not claim success', async () => {
    const factory = new IDBFactory(); const store = new DraftStore(factory, 'completion'); const record = await store.claim(key);
    // Fault injection at the native transaction boundary, using the real adapter and fake-indexeddb engine.
    const db = await new Promise<IDBDatabase>(resolve => { const r = factory.open('completion'); r.onsuccess = () => resolve(r.result); });
    const prototype = Object.getPrototypeOf(db) as IDBDatabase;
    const original = prototype.transaction;
    let completed = false;
    prototype.transaction = function (...args: Parameters<IDBDatabase['transaction']>) {
        const tx = original.apply(this, args);
        tx.addEventListener('complete', () => { completed = true; });
        return tx;
    };
    try { await store.write({ ...record, content: 'done' }, record.fencingToken); assert.equal(completed, true); }
    finally { prototype.transaction = original; db.close(); }
    await assert.rejects(store.write({ ...record, fencingToken: -1 }, record.fencingToken), StaleDraftFenceError);
    assert.equal((await store.read(key))?.content, 'done');
});
test('fixed pending is committed before sending; newer input survives successful save through Vue reactive state', async () => {
    const f = setup(); await f.session.open(scope, 'doc', 'alice');
    f.session.edit('sent'); await f.session.flush();
    const disk = deferred<void>(); f.store.gate = disk.promise;
    const response = deferred<Response>(); f.setMutation(() => response.promise);
    const saving = f.session.save(); await new Promise(r => setImmediate(r));
    assert.equal(f.writes.length, 0); assert.equal(f.session.localStatus.kind, 'writing');
    f.session.edit('later input'); const pending = draftDTO((await f.store.read(key))!).pending;
    assert.equal(pending, null);
    disk.resolve();
    while (!f.writes.length) await new Promise(r => setImmediate(r));
    assert.equal((await f.store.read(key))?.pending?.request.operation_id, 'op-1');
    f.setHead(doc('sent', 'r2')); response.resolve(json(receipt('op-1'))); await saving; await f.session.flush();
    assert.equal(f.editor.draft, 'later input'); assert.equal(f.editor.revision, 'r2'); assert.equal(f.editor.dirty, true);
    assert.equal((await f.store.read(key))?.pending, null);
});
test('failed local pending commit prevents sending and keeps memory for retry', async () => {
    const f = setup(); await f.session.open(scope, 'doc', 'alice'); f.session.edit('mine'); await f.session.flush();
    f.store.failure = true; await assert.rejects(f.session.save());
    assert.equal(f.writes.length, 0); assert.equal(f.session.localStatus.kind, 'error');
    await assert.rejects(f.session.leave()); assert.equal(f.editor.draft, 'mine'); assert.equal(f.releases(), 0);
    f.store.failure = false; await f.session.retryPending(); assert.equal(f.writes[0].operation_id, 'op-1');
});
test('unknown request, null status and later rejection preserve exact request; new head enters comparison', async () => {
    const f = setup(); await f.session.open(scope, 'doc', 'alice'); f.session.edit('sent');
    f.setMutation(async () => { throw new Error('offline'); }); await assert.rejects(f.session.save());
    f.session.edit('later'); await f.session.reconcilePending(); assert.equal(f.session.pending?.request.operation_id, 'op-1');
    f.setMutation(async () => json({ error: { code: 'forbidden', message: 'denied' } }, 403)); await assert.rejects(f.session.retryPending());
    assert.deepEqual(f.writes[0], f.writes[1]); assert.equal(f.session.pending?.request.operation_id, 'op-1');
    f.setHead(doc('someone else', 'r3')); f.setStatus(async () => json(receipt('op-1', 'r3'))); await f.session.reconcilePending();
    assert.equal(f.editor.draft, 'later'); assert.equal(f.editor.latest?.revision_id, 'r3'); assert.equal(f.editor.conflict, true); assert.equal(f.session.pending, null);
});
test('first definite rejection releases pending, mismatched success receipt does not', async () => {
    const f = setup(); await f.session.open(scope, 'doc', 'alice'); f.session.edit('mine');
    f.setMutation(async () => json({ error: { code: 'conflict', message: 'old base' } }, 409)); await assert.rejects(f.session.save());
    assert.equal(f.session.pending, null); assert.equal(f.editor.draft, 'mine'); assert.equal(f.editor.conflict, true);
    await f.session.discardDraft(); f.session.edit('again');
    f.setMutation(async () => json(receipt('other'))); await assert.rejects(f.session.save(), (e: ApiError) => e.code === 'response');
    assert.equal((f.session.pending as PendingOperation | null)?.request.operation_id, 'op-2');
});
test('open authenticates original account before local access and requires explicit continuation', async () => {
    const f = setup(); await seed(f.store); f.setIdentity('bob');
    await assert.rejects(f.session.open(scope, 'doc', 'alice')); assert.equal(f.session.localOnlyContent, null); assert.equal(f.editor.document, null);
    f.setIdentity('alice'); await f.session.open(scope, 'doc', 'alice'); assert.equal(f.editor.draft, 'original'); assert.equal(f.session.recovery.kind, 'draft');
    await f.session.continueDraft(); assert.equal(f.editor.draft, 'saved local');
    await f.session.leave(); f.deny(); await f.session.open(scope, 'doc', 'alice');
    assert.equal(f.editor.document, null); assert.equal(f.session.localOnlyContent, 'saved local');
    await assert.rejects(f.session.save()); await f.session.discardDraft(); assert.equal((await f.store.read(key))?.content, null);
});
test('recovered pending remains uncertain after a rejection and confirmed draft is not auto-loaded', async () => {
    const f = setup(); await seed(f.store, true); await f.session.open(scope, 'doc', 'alice');
    assert.equal(f.session.recovery.kind, 'pending'); assert.equal(f.editor.draft, 'original');
    assert.throws(() => f.session.edit('overwrite recovered'));
    f.setMutation(async () => json({ error: { code: 'unauthenticated', message: 'expired' } }, 401)); await assert.rejects(f.session.retryPending());
    assert.equal(f.session.pending?.request.operation_id, 'old-op');
    f.setHead(doc('sent', 'r2')); f.setStatus(async () => json(receipt('old-op'))); await f.session.reconcilePending();
    assert.equal(f.editor.draft, 'sent'); assert.equal(f.session.recovery.kind, 'draft');
    await f.session.continueDraft(); assert.equal(f.editor.draft, 'saved local');
});
test('known restore success with denied base history preserves local text for comparison', async () => {
    const f = setup(); await f.session.open(scope, 'doc', 'alice'); f.denyHistory();
    await f.session.restore('source', 'r1');
    assert.equal(f.session.pending, null); assert.equal(f.editor.draft, 'original'); assert.equal(f.editor.latest?.content, 'restored'); assert.equal(f.session.recovery.kind, 'comparison');
});
test('leave drains writes before releasing; stale response cannot repopulate another document', async () => {
    const f = setup(); await f.session.open(scope, 'doc', 'alice'); f.session.edit('sent');
    const response = deferred<Response>(); f.setMutation(() => response.promise); const saving = f.session.save();
    while (!f.writes.length) await new Promise(r => setImmediate(r));
    const disk = deferred<void>(); f.store.gate = disk.promise; f.session.edit('last');
    const leaving = f.session.leave(); await new Promise(r => setImmediate(r));
    assert.equal(f.releases(), 0); assert.equal(f.editor.draft, 'last'); assert.throws(() => f.session.edit('too late'));
    disk.resolve(); await leaving; response.resolve(json(receipt('op-1'))); await saving;
    assert.equal(f.releases(), 1); assert.equal(f.editor.document, null); assert.equal((await f.store.read(key))?.content, 'last');
});
test('Web Locks unavailability is read-only and lease stays held until explicit release', async () => {
    assert.equal(await acquireDraftLease(key, null), null);
    let held = false;
    const locks = { request: async (_name: string, _options: unknown, callback: (lock: object | null) => Promise<void>) => { if (held) return callback(null); held = true; try { await callback({}); } finally { held = false; } } } as unknown as LockManager;
    const first = await acquireDraftLease(key, locks); assert.ok(first);
    assert.equal(await acquireDraftLease(key, locks), null); await first.release(); assert.equal(held, false);
    const f = setup(); const readonly = new DocumentSession(new ApiClient(async url => json(String(url).includes('bootstrap') ? bootstrap() : { document: doc(), access })), f.editor, f.store, { acquireLease: async () => null });
    await readonly.open(scope, 'doc', 'alice'); assert.equal(readonly.leaseHeld, false); assert.equal(readonly.localStatus.kind, 'readonly'); assert.throws(() => readonly.edit('blocked'));
});


test('explicit original-account local-only deletion also removes unresolved pending without server mutation', async () => {
    const f = setup(); await seed(f.store, true); f.deny(); await f.session.open(scope, 'doc', 'alice');
    assert.equal(f.session.pending?.request.operation_id, 'old-op');
    f.setIdentity('bob'); await assert.rejects(f.session.discardDraft()); assert.equal((await f.store.read(key))?.pending?.request.operation_id, 'old-op');
    f.setIdentity('alice'); await f.session.discardDraft(); assert.equal((await f.store.read(key))?.pending, null); assert.equal(f.session.localOnlyContent, null); assert.deepEqual(f.writes, []);
});


test('late claim cannot restore a departed document or expose another account local text', async () => {
    const f = setup(); await seed(f.store);
    const gate = deferred<void>(); const entered = deferred<void>(); const claim = f.store.claim.bind(f.store);
    f.store.claim = async k => { const record = await claim(k); entered.resolve(); await gate.promise; return record; };
    const opening = f.session.open(scope, 'doc', 'alice'); await entered.promise;
    await f.session.leave(); f.editor.setIdentity('bob', { discard: true }); gate.resolve(); await opening;
    assert.equal(f.session.localOnlyContent, null); assert.equal(f.session.pending, null); assert.equal(f.editor.document, null); assert.equal(f.session.leaseHeld, false);
});


test('recovered successful restore does not offer its superseded base as a new draft', async () => {
    const f = setup(); const record = await f.store.claim(key);
    await f.store.write({ ...record, content: 'original', baseRevisionId: 'r1', pending: { kind: 'restore', request: { object_id: 'doc', operation_id: 'restore-old', source_revision_id: 'source', expected_revision_id: 'r1' } } }, record.fencingToken);
    f.setHead(doc('restored', 'r2')); f.setStatus(async () => json(receipt('restore-old', 'r2', 'restore')));
    await f.session.open(scope, 'doc', 'alice'); await f.session.continueDraft();
    assert.equal(f.editor.draft, 'restored'); assert.equal(f.editor.dirty, false); assert.equal(f.session.pending, null);
});


test('acknowledged unstored-loss exit drains writes, hides memory, allows logout and ignores late callbacks', async () => {
    const f = setup(); await f.session.open(scope, 'doc', 'alice'); f.session.edit('persisted');
    const response = deferred<Response>(); f.setMutation(() => response.promise);
    const saving = f.session.save();
    while (!f.writes.length) await new Promise(resolve => setImmediate(resolve));
    const stored = await f.store.read(key);
    const identity = new SessionState(f.client, f.editor, { beforeLeaveIdentity: () => f.session.leave() });
    f.store.failure = true; f.session.edit('unstored');
    await assert.rejects(identity.logout(), /disk full/);
    assert.equal(f.editor.draft, 'unstored'); assert.equal(f.session.leaseHeld, true); assert.equal(f.releases(), 0);
    const disk = deferred<void>(); f.store.gate = disk.promise; f.session.edit('last unsaved');
    await new Promise(resolve => setImmediate(resolve));
    const leaving = f.session.leave({ acknowledgeUnstoredLoss: true });
    assert.throws(() => f.session.edit('blocked'));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(f.releases(), 0); // An already-started write must settle before releasing ownership.
    disk.resolve(); await leaving;
    assert.equal(f.editor.document, null); assert.equal(f.editor.draft, ''); assert.equal(f.session.leaseHeld, false);
    assert.equal(f.releases(), 1); assert.notEqual(f.session.localStatus.kind, 'stored');
    assert.deepEqual(await f.store.read(key), stored);
    assert.equal(await identity.logout(), true); assert.equal(identity.user, null); assert.equal(f.client.nonce, 'anonymous');
    response.resolve(json(receipt('op-1'))); await saving;
    assert.equal(f.editor.document, null); assert.equal(f.session.pending, null);
    assert.deepEqual(await f.store.read(key), stored);
});

test('explicit manual merge persists adopted base and reopens unchanged server without a false conflict', async () => {
    const s = setup();
    await s.session.open(scope, 'doc', 'alice'); s.session.edit('local before merge'); await s.session.flush();
    s.setHead(doc('server update', 'r2'));
    await s.session.open(scope, 'doc', 'alice'); await s.session.continueDraft();
    assert.equal(s.editor.conflict, true);
    assert.equal((await s.store.read(key))?.baseRevisionId, 'r1');
    await s.session.startMerge();
    assert.equal(s.editor.comparisonDraft, 'local before merge');
    assert.equal(s.editor.draft, 'server update');
    s.session.edit('manually merged'); await s.session.flush();
    assert.equal((await s.store.read(key))?.baseRevisionId, 'r2');
    s.session.dismissComparison();
    await s.session.open(scope, 'doc', 'alice');
    assert.deepEqual(s.session.recovery, {kind: 'draft', baseStale: false});
    await s.session.continueDraft();
    assert.equal(s.editor.draft, 'manually merged'); assert.equal(s.editor.conflict, false);
});


test('foreign expiration bootstrap releases local-only ownership and preserves original account cache', async () => {
    for (const anonymousPause of [false, true]) {
        const f = setup(); await seed(f.store); f.deny();
        const identity = new SessionState(f.client, f.editor, {
            retainedIdentity: () => f.session.retainedIdentity,
            beforeLeaveIdentity: () => f.session.leave(),
        });
        await identity.bootstrap(); await f.session.open(scope, 'doc', 'alice');
        const stored = await f.store.read(key);
        if (anonymousPause) {
            f.pause(); await identity.expire();
            assert.equal(identity.user, null); assert.equal(f.editor.identity, null); assert.equal(f.editor.owner, null);
            assert.equal(f.session.localOnlyContent, 'saved local'); assert.equal(f.session.leaseHeld, true);
            f.setIdentity('alice'); await identity.bootstrap();
            assert.equal(f.session.localOnlyContent, 'saved local'); assert.equal(f.releases(), 0);
            f.pause(); await identity.expire();
        }
        f.setIdentity('bob');
        const disk = deferred<void>(); const flush = f.store.flush.bind(f.store);
        f.store.flush = async () => { await disk.promise; await flush(); };
        const accepting = anonymousPause ? identity.bootstrap() : identity.expire();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(identity.user, null); assert.equal(f.editor.paused, true);
        assert.equal(f.session.localOnlyContent, 'saved local'); assert.equal(f.releases(), 0);
        disk.resolve(); await accepting;
        assert.equal((identity.user as { id: string } | null)?.id, 'bob'); assert.equal(f.editor.identity, 'bob');
        assert.equal(f.session.localOnlyContent, null); assert.equal(f.session.leaseHeld, false);
        assert.equal(f.releases(), 1); assert.deepEqual(await f.store.read(key), stored);
    }
});

test('unresolved merge reference blocks navigation and identity departure until explicit disposition', async () => {
    const f = setup();
    const identity = new SessionState(f.client, f.editor, {
        retainedIdentity: () => f.session.retainedIdentity,
        beforeLeaveIdentity: () => f.session.leave(),
    });
    await identity.bootstrap(); await f.session.open(scope, 'doc', 'alice');
    f.session.edit('unique unsaved Alice text'); await f.session.flush();
    f.setHead(doc('server update', 'r2'));
    await f.session.open(scope, 'doc', 'alice'); await f.session.continueDraft(); await f.session.startMerge();
    const stored = await f.store.read(key); const releases = f.releases();
    assert.equal(stored?.content, 'server update');
    await assert.rejects(f.session.open(scope, 'doc', 'alice'), /合并前/);
    await assert.rejects(identity.logout(), /合并前/);
    await assert.rejects(f.session.leave({ acknowledgeUnstoredLoss: true }), /合并前/);
    await assert.rejects(f.session.discardDraft(), /合并前/);
    assert.equal(f.editor.comparisonDraft, 'unique unsaved Alice text');
    assert.equal(f.session.leaseHeld, true); assert.equal(f.releases(), releases);
    assert.deepEqual(await f.store.read(key), stored);
    f.setIdentity('bob'); await assert.rejects(identity.expire(), /合并前/);
    assert.equal(identity.user, null); assert.equal(f.editor.paused, true);
    assert.equal(f.editor.comparisonDraft, 'unique unsaved Alice text');
    await assert.rejects(identity.acceptPending({ discard: true }), /合并前/);
    f.session.dismissComparison();
    assert.deepEqual(await f.store.read(key), stored);
    assert.equal(await identity.acceptPending({ discard: true }), true);
    assert.equal((identity.user as { id: string } | null)?.id, 'bob'); assert.equal(f.editor.document, null);
    assert.equal(f.session.leaseHeld, false); assert.deepEqual(await f.store.read(key), stored);
    f.setIdentity('alice'); await identity.bootstrap(); await f.session.open(scope, 'doc', 'alice');
    await f.session.continueDraft(); assert.equal(f.editor.draft, 'server update');
    f.session.edit('ordinary durable draft'); await f.session.flush(); await identity.logout();
    assert.equal((await f.store.read(key))?.content, 'ordinary durable draft');
});

test('successful save and another merge cannot silently erase an unresolved original reference', async () => {
    const f = setup(); await f.session.open(scope, 'doc', 'alice');
    f.session.edit('unique original'); await f.session.flush(); f.setHead(doc('new head', 'r2'));
    await f.session.open(scope, 'doc', 'alice'); await f.session.continueDraft(); await f.session.startMerge();
    f.session.edit('partly merged'); await f.session.save();
    assert.equal(f.editor.comparisonDraft, 'unique original');
    assert.equal(f.editor.draft, 'partly merged'); assert.equal(f.editor.dirty, false);
    f.editor.setLatest(doc('another head', 'r3'));
    await assert.rejects(f.session.startMerge(), /合并前/);
    assert.equal(f.editor.comparisonDraft, 'unique original');
    f.session.dismissComparison(); await f.session.leave();
    assert.equal((await f.store.read(key))?.content, 'partly merged');
});
