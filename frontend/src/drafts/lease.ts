import { encodeDraftKey, type DraftKey } from './types.ts';
export interface DraftLease { release(): Promise<void> }
export function acquireDraftLease(key: DraftKey, locks: LockManager | null | undefined = globalThis.navigator?.locks): Promise<DraftLease | null> {
    if (!locks) return Promise.resolve(null);
    return new Promise(resolve => {
        let unlock!: () => void;
        const held = new Promise<void>(done => { unlock = done; });
        const completed = locks.request('c156-draft:' + encodeDraftKey(key), { mode: 'exclusive', ifAvailable: true }, async lock => {
            if (!lock) { resolve(null); return; }
            resolve({ async release() { unlock(); await completed; } });
            await held;
        });
        void completed.catch(() => resolve(null));
    });
}
