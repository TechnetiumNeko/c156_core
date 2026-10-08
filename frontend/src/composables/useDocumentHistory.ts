import { ref } from 'vue';
import type { ApiClient } from '../api/client.ts';
import type { RevisionSummary, RevisionView } from '../api/types.ts';

// Historical text stays here; this composable never writes an editor buffer.
export function useDocumentHistory(client: ApiClient) {
  const revisions = ref<RevisionSummary[]>([]); const selected = ref<RevisionView | null>(null);
  const cursor = ref<string | null>(null); const head = ref(''); const diff = ref<string | null>(null);
  const busy = ref(false); let generation = 0;
  function reset() { generation++; revisions.value = []; selected.value = null; cursor.value = null; head.value = ''; diff.value = null; busy.value = false; }
  async function list(id: string, more = false) {
    const ticket = generation; busy.value = true;
    try { const value = await client.listDocumentRevisions(id, more ? cursor.value ?? undefined : undefined);
      if (ticket !== generation) return;
      revisions.value = more ? [...revisions.value, ...value.revisions] : value.revisions;
      cursor.value = value.next_cursor; head.value = value.head_revision_id;
    } finally { if (ticket === generation) busy.value = false; }
  }
  async function view(id: string, revision: string) {
    const ticket = ++generation; busy.value = true; selected.value = null; diff.value = null;
    try { const value = await client.readDocumentRevision(id, revision); if (ticket === generation) selected.value = value; }
    finally { if (ticket === generation) busy.value = false; }
  }
  async function compare(id: string, from: string, to: string) {
    const ticket = generation; busy.value = true;
    try { const value = await client.compareDocumentRevisions(id, from, to); if (ticket === generation) diff.value = value.diff; }
    finally { if (ticket === generation) busy.value = false; }
  }
  return { revisions, selected, cursor, head, diff, busy, reset, list, view, compare };
}
