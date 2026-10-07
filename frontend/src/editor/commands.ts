import type { EditorView } from '@codemirror/view';
export type Format = 'heading' | 'bold' | 'italic' | 'list' | 'quote' | 'link' | 'code';
export function format(view: EditorView, kind: Format) {
  const range = view.state.selection.main;
  const selected = view.state.doc.sliceString(range.from, range.to);
  const line = view.state.doc.lineAt(range.from);
  if (['heading', 'list', 'quote'].includes(kind)) {
    const prefix = {heading: '## ', list: '- ', quote: '> '}[kind as 'heading' | 'list' | 'quote'];
    const text = view.state.doc.sliceString(line.from, view.state.doc.lineAt(range.to).to);
    const value = text.split('\n').map(part => prefix + part).join('\n');
    view.dispatch({changes: {from: line.from, to: line.from + text.length, insert: value}, selection: {anchor: range.from + prefix.length}});
  } else {
    const pairs = {bold: ['**', '**', '粗体'], italic: ['*', '*', '斜体'], link: ['[', '](https://)', '链接文字'], code: ['`', '`', '代码']} as const;
    const [left, right, placeholder] = pairs[kind as keyof typeof pairs];
    const text = selected || placeholder;
    view.dispatch({changes: {from: range.from, to: range.to, insert: left + text + right}, selection: {anchor: range.from + left.length, head: range.from + left.length + text.length}});
  }
  view.focus();
}
