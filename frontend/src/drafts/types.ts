import type { RestoreDocumentOperation, SaveDocumentOperation } from '../api/types.ts';
export interface DraftKey { userId: string; workspaceId: string; branchId: string; objectId: string }
export type PendingOperation =
    | Readonly<{ kind: 'save'; request: Readonly<SaveDocumentOperation> }>
    | Readonly<{ kind: 'restore'; request: Readonly<RestoreDocumentOperation> }>;
export interface DraftRecord {
    formatVersion: 1; key: DraftKey; fencingToken: number; generation: number;
    baseRevisionId: string | null; content: string | null; pending: PendingOperation | null;
}
export const encodeDraftKey = (key: DraftKey) => JSON.stringify([key.userId, key.workspaceId, key.branchId, key.objectId]);
// Explicit DTO projection also unwraps Vue proxies without cloning their internals.
export function pendingDTO(value: PendingOperation): PendingOperation {
    const r = value.request;
    const common = { object_id: r.object_id, expected_revision_id: r.expected_revision_id, operation_id: r.operation_id };
    return value.kind === 'save'
        ? Object.freeze({ kind: 'save', request: Object.freeze({ ...common, content: value.request.content }) })
        : Object.freeze({ kind: 'restore', request: Object.freeze({ ...common, source_revision_id: value.request.source_revision_id }) });
}
export function draftDTO(value: DraftRecord): DraftRecord {
    const k = value.key;
    return { formatVersion: 1, key: { userId: k.userId, workspaceId: k.workspaceId, branchId: k.branchId, objectId: k.objectId },
        fencingToken: value.fencingToken, generation: value.generation, baseRevisionId: value.baseRevisionId,
        content: value.content, pending: value.pending ? pendingDTO(value.pending) : null };
}
export interface DraftStorage {
    claim(key: DraftKey): Promise<DraftRecord>;
    read(key: DraftKey): Promise<DraftRecord | null>;
    write(record: DraftRecord, expectedToken: number): Promise<void>;
    clear(key: DraftKey, expectedToken: number): Promise<DraftRecord>;
    flush(): Promise<void>;
}
