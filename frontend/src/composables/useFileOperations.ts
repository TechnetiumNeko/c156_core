import { ref } from 'vue';
import { ApiClient, ApiError } from '../api/client.ts';
import type { Node } from '../api/types.ts';
import type { DirectoryState } from '../state/directory.ts';
import type { EditorState } from '../state/editor.ts';
import { errorText, deleteErrorText } from './errors.ts';
import type { SessionState } from '../state/session.ts';
export type FileAction = 'document' | 'folder' | 'rename' | 'delete' | 'edit';
export function useFileOperations(client: ApiClient, session: SessionState, directory: DirectoryState, editor: EditorState, select: (id: string) => Promise<void>, failure: (error: unknown, epoch: number) => Promise<void>, confirm: (title: string, content: string) => Promise<boolean>) {
  const busy = ref(false); const visible = ref(false); const name = ref(''); const message = ref('');
  const privateDocument = ref(false); const error = ref(''); const messageType = ref<'info' | 'error'>('info');
  const action = ref<'document' | 'folder' | 'rename'>('document'); const target = ref<Node | null>(null);
  function close() {if (!busy.value) visible.value = false;}
  async function refresh(parent: string | null, epoch: number) {
    if (!parent || epoch !== session.epoch) return;
    try {await directory.loadChildren(parent);}
    catch (error) {if (epoch === session.epoch) {message.value = '操作已完成，目录刷新失败。请收起后重新展开目录，勿重复提交。'; await failure(error, epoch);}}
  }
  async function open(kind: FileAction, node: Node) {
    if (busy.value || editor.saving) return;
    if (kind === 'edit') {await select(node.id); return;}
    if (kind !== 'delete') {privateDocument.value = false; error.value = ''; messageType.value = 'info'; action.value = kind; target.value = node; name.value = kind === 'rename' ? node.name : ''; message.value = ''; visible.value = true; return;}
    const epoch = session.epoch; busy.value = true; message.value = ''; messageType.value = 'info'; let submitted = false;
    try {
      const plan = node.kind === 'folder' ? await client.prepareDelete(node.id) : undefined;
      if (epoch !== session.epoch) return;
      const affectsEditor = editor.document && (node.id === editor.document.id || plan?.items.some(item => item.node.id === editor.document!.id));
      const draftWarning = affectsEditor && editor.hasUnsavedWork ? '当前文档有未保存的草稿或合并参考，删除后将丢弃，请先复制需要保留的内容。' : '';
      const count = plan ? `目录及其子项共 ${plan.items.length} 项。` : '';
      if (!await confirm('删除“' + node.name + '”', `${count}${draftWarning}确定删除？`)) return;
      if (epoch !== session.epoch || editor.saving) return;
      submitted = true;
      await client.deleteNode(node.id, plan?.version ?? node.version, plan);
      if (affectsEditor) editor.setIdentity(session.user?.id ?? null, {discard: true});
      // Invalidate affected descendants before reloading their parent.
      for (const id of [node.id, ...(plan?.items.map(item => item.node.id) ?? [])]) {delete directory.children[id]; delete directory.expanded[id]; delete directory.errors[id];}
      message.value = '已删除。'; await refresh(node.parent_id, epoch);
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === 'stale') return;
      if (caught instanceof ApiError && caught.status === 401) await failure(caught, epoch);
      else if (epoch === session.epoch) {message.value = deleteErrorText(caught, node.kind === 'folder', submitted); messageType.value = 'error';}
    }
    finally {busy.value = false;}
  }
  async function submit() {
    if (busy.value || editor.saving || !target.value || !name.value.trim()) return;
    const node = target.value; const epoch = session.epoch; busy.value = true; error.value = ''; messageType.value = 'info';
    try {
      if (action.value === 'rename') {
        const result = await client.renameNode(node.id, name.value, node.version);
        if (editor.document?.id === node.id) Object.assign(editor.document, result.node);
        visible.value = false; message.value = '名称已更新。'; await refresh(node.parent_id, epoch);
      } else {
        const result = action.value === 'folder' ? await client.createFolder(node.id, name.value) : await client.createDocument(node.id, name.value, privateDocument.value ? 'private' : 'inherit');
        visible.value = false; message.value = action.value === 'folder' ? '文件夹已创建。' : '文档已创建。';
        directory.expanded[node.id] = true; await refresh(node.id, epoch);
        if ('document' in result && epoch === session.epoch) await select(result.document.id);
      }
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === 'stale') return;
      if (caught instanceof ApiError && caught.status === 401) await failure(caught, epoch);
      else if (epoch === session.epoch) error.value = errorText(caught);
    }
    finally {busy.value = false;}
  }
  function reset() {privateDocument.value = false; error.value = ''; messageType.value = 'info'; visible.value = false; target.value = null; name.value = ''; message.value = '';}
  return {busy, visible, name, action, message, privateDocument, error, messageType, open, submit, close, reset};
}
