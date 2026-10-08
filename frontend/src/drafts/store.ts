import { markRaw } from 'vue';
import { draftDTO, encodeDraftKey, type DraftKey, type DraftRecord, type DraftStorage } from './types.ts';
export class StaleDraftFenceError extends Error {
    constructor() { super('草稿编辑资格已变更，旧写入已停止。'); this.name = 'StaleDraftFenceError'; }
}
export class DraftStore implements DraftStorage {
    private database: Promise<IDBDatabase> | null = null;
    private transactions = new Set<Promise<unknown>>();
    private factory: IDBFactory | undefined;
    private name: string;
    constructor(factory = globalThis.indexedDB, name = 'c156-drafts') {
        this.factory = factory; this.name = name; markRaw(this);
    }
    private connect(): Promise<IDBDatabase> {
        if (!this.database) this.database = new Promise((resolve, reject) => {
            if (!this.factory) { reject(new Error('浏览器不支持本机草稿存储。')); return; }
            const request = this.factory.open(this.name, 1);
            request.onupgradeneeded = () => request.result.createObjectStore('drafts');
            request.onerror = () => reject(request.error);
            request.onblocked = () => reject(new Error('本机草稿数据库被其他页面阻塞。'));
            request.onsuccess = () => { request.result.onversionchange = () => request.result.close(); resolve(request.result); };
        });
        return this.database;
    }
    private transact<T>(mode: IDBTransactionMode, work: (store: IDBObjectStore, result: (value: T) => void, fail: (error: unknown) => void) => void): Promise<T> {
        const task = this.connect().then(db => new Promise<T>((resolve, reject) => {
            const tx = db.transaction('drafts', mode);
            let value: T;
            let failure: unknown;
            tx.oncomplete = () => resolve(value);
            tx.onabort = () => reject(failure ?? tx.error ?? new Error('本机草稿事务已中止。'));
            tx.onerror = () => { failure ??= tx.error; };
            const fail = (error: unknown) => { failure = error; tx.abort(); };
            try { work(tx.objectStore('drafts'), v => { value = v; }, fail); } catch (error) { fail(error); }
        }));
        this.transactions.add(task);
        void task.then(() => this.transactions.delete(task), () => this.transactions.delete(task));
        return task;
    }
    read(key: DraftKey) {
        const encoded = encodeDraftKey(key);
        return this.transact<DraftRecord | null>('readonly', (store, result) => {
            const request = store.get(encoded); request.onsuccess = () => result(request.result ?? null);
        });
    }
    claim(key: DraftKey) {
        const captured = { ...key }; const encoded = encodeDraftKey(captured);
        return this.transact<DraftRecord>('readwrite', (store, result, fail) => {
            const request = store.get(encoded);
            request.onsuccess = () => {
                try {
                    const old: DraftRecord | undefined = request.result;
                    const value = old ? draftDTO(old) : { formatVersion: 1 as const, key: captured, fencingToken: 0, generation: 0, baseRevisionId: null, content: null, pending: null };
                    value.fencingToken++;
                    store.put(value, encoded); result(value);
                } catch (error) { fail(error); }
            };
        });
    }
    write(record: DraftRecord, expectedToken: number) {
        const captured = draftDTO(record); const encoded = encodeDraftKey(captured.key);
        return this.transact<void>('readwrite', (store, result, fail) => {
            const request = store.get(encoded);
            request.onsuccess = () => {
                try {
                    const old: DraftRecord | undefined = request.result;
                    if (!old || old.fencingToken !== expectedToken || captured.fencingToken !== expectedToken || old.generation > captured.generation) throw new StaleDraftFenceError();
                    store.put(captured, encoded); result();
                } catch (error) { fail(error); }
            };
        });
    }
    clear(key: DraftKey, expectedToken: number) {
        const encoded = encodeDraftKey(key);
        return this.transact<DraftRecord>('readwrite', (store, result, fail) => {
            const request = store.get(encoded);
            request.onsuccess = () => {
                try {
                    const old: DraftRecord | undefined = request.result;
                    if (!old || old.fencingToken !== expectedToken) throw new StaleDraftFenceError();
                    const value: DraftRecord = { ...draftDTO(old), fencingToken: old.fencingToken + 1, generation: old.generation + 1, content: null, baseRevisionId: null, pending: null };
                    store.put(value, encoded); result(value);
                } catch (error) { fail(error); }
            };
        });
    }
    async flush() { while (this.transactions.size) await Promise.all([...this.transactions]); }
}
