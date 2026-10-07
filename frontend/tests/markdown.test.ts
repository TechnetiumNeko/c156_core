import test from 'node:test';
import assert from 'node:assert/strict';
import { EditorState } from '@codemirror/state';
import { markdown } from '@codemirror/lang-markdown';
import { GFM } from '@lezer/markdown';
import { previewDecorations } from '../src/editor/livePreview.ts';

test('live formatting preserves Markdown and reveals syntax touched by the selection', () => {
  const text = '# 标题\n\n**正文**';
  const state = EditorState.create({doc: text, extensions: [markdown({extensions: GFM})], selection: {anchor: 3}});
  const hidden: [number, number][] = [];
  previewDecorations(state).between(0, text.length, (from, to, value) => {if (!value.spec.class && from < to) hidden.push([from, to]);});
  assert.deepEqual(hidden, [[6, 8], [10, 12]]);
  assert.equal(state.doc.toString(), text);
  const selected = state.update({selection: {anchor: 0, head: text.length}}).state;
  const selectedHidden: number[] = [];
  previewDecorations(selected).between(0, text.length, (from, to, value) => {if (!value.spec.class && from < to) selectedHidden.push(from);});
  assert.deepEqual(selectedHidden, []);
  assert.equal(selected.doc.toString(), text);
});
