import { EditorState, EditorSelection } from '@codemirror/state';
import { Decoration, ViewPlugin, WidgetType, type DecorationSet, type EditorView, type ViewUpdate } from '@codemirror/view';
import { syntaxTree } from '@codemirror/language';
class SymbolWidget extends WidgetType {
  symbol: string;
  constructor(symbol: string) { super(); this.symbol = symbol; }
  eq(other: SymbolWidget) { return this.symbol === other.symbol; }
  toDOM() { const span = document.createElement('span'); span.textContent = this.symbol; span.className = 'cm-md-symbol'; return span; }
}
const inlineStyles: Record<string, string> = {StrongEmphasis: 'cm-md-strong', Emphasis: 'cm-md-em', Strikethrough: 'cm-md-strike', InlineCode: 'cm-md-code', Link: 'cm-md-link'};
const markers = new Set(['HeaderMark', 'EmphasisMark', 'StrikethroughMark', 'CodeMark', 'CodeInfo', 'LinkMark', 'URL']);
function active(selection: EditorSelection, from: number, to: number) { return selection.ranges.some(range => range.from <= to && range.to >= from); }
export function previewDecorations(state: EditorState, ranges: readonly {from: number; to: number}[] = [{from: 0, to: state.doc.length}]): DecorationSet {
  const decorations: ReturnType<Decoration['range']>[] = [];
  const seen = new Set<string>();
  for (const range of ranges) syntaxTree(state).iterate({from: range.from, to: range.to, enter(ref) {
    const {name, from, to} = ref;
    const key = `${name}:${from}:${to}`; if (seen.has(key)) return; seen.add(key);
    if (/^ATXHeading[1-6]$/.test(name)) decorations.push(Decoration.line({class: `cm-md-heading cm-md-h${name.slice(-1)}`}).range(state.doc.lineAt(from).from));
    const style = inlineStyles[name];
    if (style && from < to) decorations.push(Decoration.mark({class: style}).range(from, to));
    if (name === 'FencedCode' || name === 'Blockquote') {
      const endLine = state.doc.lineAt(to).number;
      for (let line = state.doc.lineAt(from).number; line <= endLine; line++) {
        const position = state.doc.line(line).from;
        if (position >= range.from && position <= range.to) decorations.push(Decoration.line({class: name === 'FencedCode' ? 'cm-md-code-line' : 'cm-md-quote'}).range(position));
      }
    }
    if (!markers.has(name) && !['ListMark', 'QuoteMark', 'TaskMarker'].includes(name)) return;
    const parent = ref.node.parent;
    if (!parent || active(state.selection, parent.from, parent.to)) return;
    // URLs in autolinks and image syntax remain source; only regular links collapse.
    if ((name === 'URL' || name === 'LinkMark') && parent.name !== 'Link') return;
    if (name === 'ListMark') {
      const value = state.doc.sliceString(from, to);
      if (/^[-+*]$/.test(value)) decorations.push(Decoration.replace({widget: new SymbolWidget('•')}).range(from, to));
    } else if (name === 'TaskMarker') decorations.push(Decoration.replace({widget: new SymbolWidget(state.doc.sliceString(from, to).toLowerCase().includes('x') ? '☑' : '☐')}).range(from, to));
    else if (from < to) decorations.push(Decoration.replace({}).range(from, to));
  }});
  return Decoration.set(decorations, true);
}
export const livePreview = ViewPlugin.fromClass(class {
  decorations: DecorationSet;
  constructor(view: EditorView) { this.decorations = previewDecorations(view.state, view.visibleRanges); }
  update(update: ViewUpdate) { this.decorations = update.view.composing ? this.decorations.map(update.changes) : previewDecorations(update.state, update.view.visibleRanges); }
}, {decorations: value => value.decorations});
