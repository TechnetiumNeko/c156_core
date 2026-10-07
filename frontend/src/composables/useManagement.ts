import { ref } from 'vue';
import { ApiClient, ApiError } from '../api/client.ts';
export function useManagement(client: ApiClient, failure: (error: unknown) => void, refresh?: () => Promise<void>) {
  const busy = ref(false); const message = ref('');
  async function run(action: () => Promise<void>) {
    if (busy.value) return;
    busy.value = true; message.value = '';
    try { await action(); }
    catch (error) {
      if (error instanceof ApiError && error.code === 'stale') return;
      if (error instanceof ApiError && error.code === 'conflict' && refresh) {try {await refresh();} catch { /* Preserve the original conflict; the page also has an explicit refresh. */ }}
      failure(error);
    } finally { busy.value = false; }
  }
  return {busy, message, run};
}
