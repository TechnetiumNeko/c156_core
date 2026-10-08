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

test('blocked folder deletion shows feedback and preserves the draft and cached directory', async () => {
  const { EditorState } = await import('../src/state/editor.ts');
  const { SessionState } = await import('../src/state/session.ts');
  const { DocumentSession } = await import('../src/state/document-session.ts');
  const { useFileOperations } = await import('../src/composables/useFileOperations.ts');
  const client = new ApiClient(async () => json({error: {code: 'forbidden', message: 'Access denied.'}}, 403));
  const editor = new EditorState(); editor.setIdentity('alice');
  editor.open({...child, content: '原文', revision_id: 'r1'}); editor.edit('未保存的草稿');
  const session = new SessionState(client, editor);
  const directory = new DirectoryState(client); directory.setRoot(root, 'alice'); directory.children.root = [child];
  const files = useFileOperations(client, session, directory, editor, async () => {}, async () => {}, async () => true, new DocumentSession(client, editor));
  await files.open('delete', root);
  assert.match(files.message.value, /权限/); assert.match(files.message.value, /未执行/);
  assert.equal(files.messageType.value, 'error'); assert.equal(files.busy.value, false);
  assert.deepEqual(directory.children.root, [child]); assert.equal(editor.draft, '未保存的草稿');
});
