import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiClient } from '../src/api/client.ts';
import { DirectoryState } from '../src/state/directory.ts';
const root = { id: 'root', kind: 'folder', name: 'Root', parent_id: null, position: 0, version: 1, path: '/', created_at: '', modified_at: '', metadata: {} };
const child = { ...root, id: 'doc', kind: 'document', access: { version: 1, actions: ['read'], visibility: 'visible', frozen: false, can_freeze: false, can_unfreeze: false } };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
test('expanded directory exposes authorized returned nodes and caches them', async () => {
  let calls = 0;
  const state = new DirectoryState(new ApiClient(async () => { calls++; return json({ nodes: [child] }); }));
  state.setRoot(root, 'alice'); await state.toggle('root');
  assert.deepEqual(state.children.root, [child]); assert.equal(state.expanded.root, true);
  await state.toggle('root'); await state.toggle('root'); assert.equal(calls, 1);
});
test('identity change prevents late children from filling cleared cache', async () => {
  let resolve!: (response: Response) => void;
  const state = new DirectoryState(new ApiClient(() => new Promise(r => { resolve = r; })));
  state.setRoot(root, 'alice'); const pending = state.toggle('root');
  state.setRoot({ ...root, id: 'other' }, 'bob'); resolve(json({ nodes: [child] })); await pending;
  assert.deepEqual(state.children, {}); assert.deepEqual(state.loading, {}); assert.equal(state.root?.id, 'other');
});
test('failed read stays an error and can be retried without becoming empty directory', async () => {
  let failed = true;
  const state = new DirectoryState(new ApiClient(async () => failed ? json({ error: { code: 'forbidden', message: 'Denied' } }, 403) : json({ nodes: [child] })));
  state.setRoot(root, 'alice'); await assert.rejects(state.toggle('root'));
  assert.equal(state.children.root, undefined); assert.equal(state.errors.root, 'Denied'); assert.equal(state.loading.root, false);
  failed = false; await state.loadChildren('root'); assert.deepEqual(state.children.root, [child]); assert.equal(state.errors.root, undefined);
});
