import { markRaw } from 'vue';
import { ApiClient, ApiError } from '../api/client.ts';
import type { Access, Document, OperationResult } from '../api/types.ts';
import { acquireDraftLease, type DraftLease } from '../drafts/lease.ts';
import { DraftStore } from '../drafts/store.ts';
import { draftDTO, pendingDTO, type DraftKey, type DraftRecord, type DraftStorage, type PendingOperation } from '../drafts/types.ts';
import { EditorState } from './editor.ts';
export type LocalStatus = { kind: 'idle' | 'writing' | 'stored' } | { kind: 'readonly' | 'error'; message: string };
export type Recovery = { kind: 'none' } | { kind: 'draft'; baseStale: boolean } | { kind: 'pending' } | { kind: 'comparison' } | { kind: 'local-only'; reason: string };
export interface DocumentScope { workspace_id: string; branch_id: string }
export interface DocumentSessionOptions {
    acquireLease?: (key: DraftKey) => Promise<DraftLease | null>;
    operationId?: () => string;
}
const normalize = (text: string) => text.replace(/\r\n?/g, '\n');
export class DocumentSession {
    localStatus: LocalStatus = { kind: 'idle' };
    recovery: Recovery = { kind: 'none' };
    leaseHeld = false;
    access: Access | null = null;
    localOnlyContent: string | null = null;
    operationError: string | null = null;
    busy = false;
    private client: ApiClient;
    private editor: EditorState;
    private store: DraftStorage;
    private acquireLease: (key: DraftKey) => Promise<DraftLease | null>;
    private operationId: () => string;
    private lease: DraftLease | null = null;
    private record: DraftRecord | null = null;
    private ownerId: string | null = null;
    get retainedIdentity(): string | null { return this.ownerId; }
    private epoch = 0;
    private queue: Promise<void> = Promise.resolve();
    private persistenceError: unknown = null;
    private available = false;
    private leaving = false;
    private uncertain = false;
    private awaitingRecovery = false;
    private clearing = false;
    get pending(): PendingOperation | null { return this.record?.pending ?? null; }
    get canEdit(): boolean {
        return this.leaseHeld && this.available && this.ownsIdentity() && !this.leaving &&
            !this.awaitingRecovery && !this.clearing && !['draft', 'local-only'].includes(this.recovery.kind);
    }
    constructor(client: ApiClient, editor: EditorState, store: DraftStorage = new DraftStore(), options: DocumentSessionOptions = {}) {
        this.client = markRaw(client); this.editor = editor; this.store = markRaw(store);
        this.acquireLease = options.acquireLease ?? acquireDraftLease;
        this.operationId = options.operationId ?? (() => crypto.randomUUID());
    }
    private current(epoch: number) { return epoch === this.epoch; }
    private ownsIdentity() { return !!this.record && this.editor.identity === this.record.key.userId && !this.editor.paused; }
    private writable() {
        if (!this.leaseHeld || !this.available || !this.ownsIdentity() || this.leaving) throw new Error('当前页面不能保存或修改此文档。');
    }
    private persist() {
        if (!this.record || !this.leaseHeld) return Promise.resolve();
        const captured = draftDTO(this.record); const token = captured.fencingToken; const epoch = this.epoch;
        this.localStatus = { kind: 'writing' };
        const task = this.queue.then(async () => {
            if (!this.current(epoch)) return;
            await this.store.write(captured, token);
            if (this.current(epoch)) { this.persistenceError = null; if (this.record?.generation === captured.generation) this.localStatus = { kind: 'stored' }; }
        });
        this.queue = task.catch(error => {
            if (epoch === this.epoch) { this.persistenceError = error; this.localStatus = { kind: 'error', message: '本机草稿未存好，请保留页面并复制文字，或重试。' }; }
        });
        return task;
    }
    async open(scope: DocumentScope, objectId: string, userId: string) {
        await this.leave();
        const epoch = ++this.epoch;
        // The supplied identity/key alone never authorizes local or server access.
        const authenticated = await this.client.bootstrap();
        if (!this.current(epoch)) return;
        if ('nonce' in authenticated || authenticated.user.id !== userId || authenticated.scope.workspace_id !== scope.workspace_id || authenticated.scope.branch_id !== scope.branch_id)
            throw new Error('当前账号或作品范围已改变，请重新打开文档。');
        this.editor.setIdentity(userId, { discard: true });
        this.ownerId = userId;
        const key: DraftKey = { userId, workspaceId: scope.workspace_id, branchId: scope.branch_id, objectId };
        let snapshot: Document | null = null;
        let localReason = '';
        try {
            const response = await this.client.readDocument(objectId);
            snapshot = response.document;
            if (this.current(epoch)) this.access = response.access;
            if (snapshot.id !== objectId) throw new ApiError('response', '文档标识不匹配。');
        } catch (error) {
            if (!(error instanceof ApiError) || !['forbidden', 'not_found'].includes(error.code)) throw error;
            localReason = error.code;
        }
        if (!this.current(epoch) || this.editor.identity !== userId) return;
        this.available = !!snapshot;
        if (snapshot) this.editor.open(snapshot);
        const lease = await this.acquireLease(key);
        if (!this.current(epoch) || this.editor.identity !== userId) { await lease?.release(); return; }
        this.lease = lease ? markRaw(lease) : null; this.leaseHeld = !!lease;
        try {
            const record = lease ? await this.store.claim(key) : await this.store.read(key);
            if (!this.current(epoch) || this.editor.identity !== userId) { await lease?.release(); return; }
            this.record = record;
        }
        catch (error) {
            await lease?.release();
            if (!this.current(epoch)) return;
            this.lease = null; this.leaseHeld = false;
            this.localStatus = { kind: 'readonly', message: '本机草稿存储不可用，此页只读。' };
            this.editor.paused = true; throw error;
        }
        if (!this.current(epoch)) return;
        this.localStatus = lease ? { kind: 'idle' } : { kind: 'readonly', message: '此页未取得编辑资格，可能已有其他页面编辑，或浏览器不支持 Web Locks。' };
        if (!lease) this.editor.paused = true;
        if (!snapshot) {
            this.localOnlyContent = this.record?.content ?? null;
            this.recovery = { kind: 'local-only', reason: localReason };
            return;
        }
        this.awaitingRecovery = this.record?.content !== null && this.record?.content !== undefined;
        if (this.record?.pending) {
            this.uncertain = true; this.editor.uncertainSave = true; this.recovery = { kind: 'pending' };
            if (lease) await this.reconcilePending();
        } else if (this.record?.content !== null && this.record?.content !== undefined) {
            this.recovery = { kind: 'draft', baseStale: this.record.baseRevisionId !== snapshot.revision_id };
        }
    }
    edit(text: string) {
        this.writable();
        if (this.awaitingRecovery || ['draft', 'local-only'].includes(this.recovery.kind) || this.clearing) throw new Error('请先选择继续或丢弃本机草稿。');
        this.editor.edit(text);
        if (!this.record) return;
        this.record.content = this.editor.draft; this.record.generation++;
        this.record.baseRevisionId ??= this.editor.revision;
        void this.persist().catch(() => {});
    }
    private requireComparisonDisposition() {
        if (this.editor.comparisonDraft !== null) throw new Error('合并前的草稿仅保留在此页面。请先合并或复制需要的文字，再明确移除参考；也可留在当前页面。');
    }
    dismissComparison() {
        // Explicit reference disposition never deletes the persisted current draft.
        this.editor.comparisonDraft = null;
    }
    async startMerge() {
        this.requireComparisonDisposition();
        this.writable();
        if (this.pending || this.busy || this.awaitingRecovery || !this.record) return;
        if (!this.editor.startMerge()) return;
        // Only explicit adoption advances the local base; unresolved comparisons retain their old base.
        this.record.baseRevisionId = this.editor.revision;
        this.record.content = this.editor.draft; this.record.generation++;
        this.recovery = { kind: 'none' };
        await this.persist();
    }
    async continueDraft() {
        this.writable();
        if (!this.record || this.pending || this.recovery.kind !== 'draft') return;
        const stale = this.recovery.baseStale;
        this.awaitingRecovery = false;
        this.editor.edit(this.record.content ?? '');
        if (stale && this.editor.document) {
            this.editor.latest = this.editor.document; this.editor.conflict = true;
            this.editor.revision = this.record.baseRevisionId;
            this.recovery = { kind: 'comparison' };
        } else this.recovery = { kind: 'none' };
    }
    async discardDraft() {
        this.requireComparisonDisposition();
        if (!this.record || !this.leaseHeld || this.leaving || this.busy) throw new Error('当前不能清理草稿。');
        const epoch = this.epoch;
        const key = { ...this.record.key }; const token = this.record.fencingToken;
        this.busy = true; this.clearing = true;
        try {
            // Local-only deletion authenticates the original account but never reads server document/history.
            const authenticated = await this.client.session();
            if (!this.current(epoch) || authenticated.user.id !== key.userId || this.editor.identity !== authenticated.user.id) throw new Error('当前账号不能清理此草稿。');
            if (this.pending && this.available) throw new Error('请先确认待处理操作的结果。');
            await this.flush();
            if (!this.current(epoch) || this.leaving) return;
            let cleared: DraftRecord;
            try { cleared = await this.store.clear(key, token); }
            catch (error) {
                if (this.current(epoch)) this.localStatus = { kind: 'error', message: '本机草稿未清理，请保留文字并重试。' };
                throw error;
            }
            if (!this.current(epoch)) return;
            this.record = cleared; this.localOnlyContent = null; this.awaitingRecovery = false;
            if (this.editor.document) this.editor.replaceDocument(this.editor.latest ?? this.editor.document);
            this.recovery = this.available ? { kind: 'none' } : { kind: 'local-only', reason: 'cleared' };
            this.localStatus = { kind: 'stored' };
        } finally { if (this.current(epoch)) { this.busy = false; this.clearing = false; } }
    }
    async save() {
        this.writable();
        if (this.pending || this.busy || this.editor.conflict || this.recovery.kind === 'draft' || !this.editor.dirty || !this.editor.revision) return;
        const request = { object_id: this.record!.key.objectId, content: this.editor.draft, expected_revision_id: this.editor.revision, operation_id: this.operationId() };
        await this.beginOperation(pendingDTO({ kind: 'save', request }));
    }
    async restore(sourceRevisionId: string, expectedRevisionId: string) {
        this.writable();
        if (this.pending || this.busy || this.editor.hasUnsavedWork || this.editor.conflict || this.recovery.kind !== 'none') throw new Error('请先处理当前草稿和待确认操作。');
        if (this.editor.revision !== expectedRevisionId) throw new ApiError('conflict', '恢复预览已过期。');
        await this.beginOperation(pendingDTO({ kind: 'restore', request: { object_id: this.record!.key.objectId, source_revision_id: sourceRevisionId, expected_revision_id: expectedRevisionId, operation_id: this.operationId() } }));
    }
    private async beginOperation(pending: PendingOperation) {
        this.busy = true; this.uncertain = false; this.operationError = null;
        const epoch = this.epoch;
        this.record!.pending = pending; this.record!.content = this.editor.draft;
        this.record!.baseRevisionId = this.editor.revision; this.record!.generation++;
        this.editor.uncertainSave = true; this.recovery = { kind: 'pending' };
        try {
            await this.persist();
            if (this.current(epoch)) await this.send(pending, epoch);
        } finally { if (epoch === this.epoch) this.busy = false; }
    }
    private async send(pending: PendingOperation, epoch: number) {
        this.writable();
        const wasUncertain = this.uncertain;
        this.uncertain = true;
        let result: OperationResult;
        try { result = pending.kind === 'save' ? await this.client.saveDocumentOperation(pending.request) : await this.client.restoreDocument(pending.request); }
        catch (error) {
            if (!this.current(epoch) || !this.ownsIdentity()) return;
            this.operationError = error instanceof ApiError ? error.code : 'network';
            if (!wasUncertain && error instanceof ApiError && error.outcome === 'rejected') {
                this.record!.pending = null; this.record!.generation++; this.uncertain = false; this.editor.uncertainSave = false;
                this.editor.conflict = error.code === 'conflict'; this.recovery = this.editor.conflict ? { kind: 'comparison' } : { kind: 'none' };
                await this.persist();
            }
            throw error;
        }
        if (this.current(epoch)) await this.confirm(pending, result, epoch);
    }
    async retryPending() {
        this.writable();
        if (!this.pending || this.busy) return;
        this.busy = true; const epoch = this.epoch; const pending = pendingDTO(this.pending);
        try {
            // A failed initial persistence is retried before the first possible send.
            if (!this.uncertain) await this.persist();
            if (this.current(epoch)) await this.send(pending, epoch);
        } finally { if (epoch === this.epoch) this.busy = false; }
    }
    async reconcilePending() {
        this.writable();
        if (!this.pending || this.busy) return;
        this.busy = true; const epoch = this.epoch; const pending = pendingDTO(this.pending);
        try {
            const result = await this.client.getOperationStatus(pending.request.object_id, pending.request.operation_id);
            if (this.current(epoch) && result.operation) await this.confirm(pending, result as OperationResult, epoch);
        } catch (error) { if (this.current(epoch)) this.operationError = error instanceof ApiError ? error.code : 'network'; throw error; }
        finally { if (epoch === this.epoch) this.busy = false; }
    }
    private async confirm(pending: PendingOperation, result: OperationResult, epoch: number) {
        if (!this.current(epoch) || !this.ownsIdentity() || this.leaving) return;
        if (result.operation.operation_id !== pending.request.operation_id || result.operation.operation_type !== pending.kind) throw new ApiError('response', '操作回执与待确认请求不一致。');
        const head = (await this.client.readDocument(pending.request.object_id)).document;
        if (head.id !== pending.request.object_id) throw new ApiError('response', '文档标识不匹配。');
        if (!this.current(epoch) || !this.ownsIdentity() || this.leaving) return;
        let restoreBase: string | null = null;
        if (pending.kind === 'restore') {
            try { restoreBase = normalize((await this.client.readDocumentRevision(head.id, pending.request.expected_revision_id)).content); }
            catch { /* A known receipt remains confirmed; preserve unclassifiable local text. */ }
        }
        if (!this.current(epoch) || !this.ownsIdentity() || this.leaving) return;
        const local = this.record!.content ?? this.editor.draft;
        const advanced = result.current_revision_id !== result.operation.result_revision_id || head.revision_id !== result.operation.result_revision_id;
        const compare = advanced || (pending.kind === 'restore' && restoreBase !== local);
        this.record!.pending = null; this.record!.generation++;
        this.editor.uncertainSave = false; this.uncertain = false; this.operationError = null;
        if (this.awaitingRecovery) {
            this.editor.replaceDocument(head);
            if (!compare) {
                this.record!.baseRevisionId = head.revision_id;
                // The unchanged pre-restore text was superseded by the confirmed restore.
                if (pending.kind === 'restore') this.record!.content = head.content;
            }
            this.recovery = { kind: 'draft', baseStale: compare };
        } else if (compare) {
            this.editor.edit(local); this.editor.latest = head; this.editor.conflict = true;
            this.recovery = { kind: 'comparison' };
        } else {
            this.editor.acceptOperation(head, pending.kind === 'save' ? local : head.content);
            this.record!.content = this.editor.draft; this.record!.baseRevisionId = head.revision_id;
            this.recovery = { kind: 'none' };
        }
        try { await this.persist(); }
        catch (error) {
            if (this.current(epoch) && this.record) { this.record.pending = pending; this.uncertain = true; this.editor.uncertainSave = true; this.recovery = { kind: 'pending' }; }
            throw error;
        }
    }
    async flush() {
        let observed: Promise<void>;
        do { observed = this.queue; await observed; } while (observed !== this.queue);
        if (this.persistenceError && this.record && this.leaseHeld) await this.persist();
        await this.store.flush();
        if (this.persistenceError) throw this.persistenceError;
    }
    async leave({ acknowledgeUnstoredLoss = false }: { acknowledgeUnstoredLoss?: boolean } = {}) {
        this.requireComparisonDisposition();
        // Only an explicit UI acknowledgement may abandon failed local writes. Default exit retains memory.
        this.leaving = true;
        try {
            if (acknowledgeUnstoredLoss) {
                // Invalidate queued writes and server callbacks before draining work already in progress.
                this.epoch++;
                await this.queue;
                try { await this.store.flush(); } catch { /* Loss was acknowledged; never clear the stored record. */ }
            } else {
                await this.flush();
                this.epoch++;
            }
            await this.lease?.release();
            this.lease = null; this.leaseHeld = false; this.record = null; this.ownerId = null; this.available = false; this.access = null;
            this.localOnlyContent = null; this.awaitingRecovery = false; this.recovery = { kind: 'none' }; this.localStatus = { kind: 'idle' }; this.busy = false;
            this.persistenceError = null; this.operationError = null;
            this.editor.hideDocument();
        } finally { this.leaving = false; }
    }
}
