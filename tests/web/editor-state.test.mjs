import test from 'node:test';
import assert from 'node:assert/strict';
import { EditorState } from '../../src/web/static/editor-state.js';
const doc = (content = 'original', revision_id = 'r1', id = 'a') => ({
  id,
  content,
  revision_id,
  path: '/a',
});
test('CRLF unchanged representation remains clean', () => {
  const s = new EditorState();
  s.open(doc('a\r\nb'));
  assert.equal(s.draft, 'a\nb');
  s.edit('a\nb');
  assert.equal(s.dirty, false);
  assert.equal(s.beginSave(), null);
});
test('typing while saving is retained and confirmed base advances', () => {
  const s = new EditorState();
  s.open(doc());
  s.edit('first');
  const p = s.beginSave();
  assert.equal(s.beginSave(), null);
  s.edit('second');
  s.saveSucceeded(p, doc('first', 'r2'));
  assert.equal(s.draft, 'second');
  assert.equal(s.revision, 'r2');
  assert.equal(s.dirty, true);
});
test('failed save keeps exact original revision and draft', () => {
  const s = new EditorState();
  s.open(doc());
  s.edit('mine');
  const p = s.beginSave();
  s.saveFailed(p, 'conflict');
  assert.equal(s.revision, 'r1');
  assert.equal(s.original, 'original');
  assert.equal(s.draft, 'mine');
  assert.equal(s.conflict, true);
});
test('stale document loads cannot replace newer selection', () => {
  const s = new EditorState();
  const a = s.beginLoad();
  const b = s.beginLoad();
  assert.equal(s.finishLoad(a, doc()), false);
  assert.equal(s.finishLoad(b, doc('b', 'r2', 'b')), true);
  assert.equal(s.document.id, 'b');
});
test('manual merge starts from latest and retains old draft independently', () => {
  const s = new EditorState();
  s.open(doc());
  s.edit('mine');
  s.setLatest(doc('theirs', 'r2'));
  assert.equal(s.revision, 'r1');
  s.startMerge();
  assert.equal(s.draft, 'theirs');
  assert.equal(s.revision, 'r2');
  assert.equal(s.comparisonDraft, 'mine');
  assert.equal(s.dirty, false);
  s.edit('merged');
  assert.equal(s.beginSave().expected_revision_id, 'r2');
});
test('late save cannot update another document', () => {
  const s = new EditorState();
  s.open(doc());
  s.edit('mine');
  const p = s.beginSave();
  s.open(doc('b', 'rb', 'b'));
  assert.equal(s.saveSucceeded(p, doc('mine', 'r2')), false);
  assert.equal(s.document.id, 'b');
});
test('unload warns during saving even after reverting to initial text', () => {
  const s = new EditorState();
  s.open(doc());
  s.edit('pending');
  const ticket = s.beginSave();
  s.edit('original');
  assert.equal(s.dirty, false);
  assert.equal(s.shouldWarnBeforeUnload, true);
  s.saveSucceeded(ticket, doc('pending', 'r2'));
  assert.equal(s.draft, 'original');
  assert.equal(s.shouldWarnBeforeUnload, true);
});
test('retained merge draft stays protected until merged text is saved', () => {
  const s = new EditorState();
  s.open(doc());
  s.edit('mine');
  s.setLatest(doc('theirs', 'r2'));
  s.startMerge();
  assert.equal(s.dirty, false);
  assert.equal(s.hasUnsavedWork, true);
  assert.equal(s.shouldWarnBeforeUnload, true);
  s.edit('merged');
  const ticket = s.beginSave();
  s.saveSucceeded(ticket, doc('merged', 'r3'));
  assert.equal(s.comparisonDraft, null);
  assert.equal(s.hasUnsavedWork, false);
  assert.equal(s.shouldWarnBeforeUnload, false);
});
