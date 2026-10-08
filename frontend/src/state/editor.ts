import type { Document, SaveDocument } from '../api/types.ts';
export interface Ticket {
    id: number;
    epoch: number;
    userId: string | null;
    selection: number;
    documentGeneration: number;
    objectId: string | null;
}
export interface SaveTicket extends Ticket, SaveDocument {
}
const normalize = (value: string) => value.replace(/\r\n?/g, '\n');
export class EditorState {
    identity: string | null = null;
    owner: string | null = null;
    epoch = 0;
    paused = true;
    uncertainSave = false;
    document: Document | null = null;
    original = '';
    initial = '';
    draft = '';
    revision: string | null = null;
    comparisonDraft: string | null = null;
    latest: Document | null = null;
    conflict = false;
    saving: SaveTicket | null = null;
    private sequence = 0;
    private selection = 0;
    private generation = 0;
    private latestId = 0;
    get dirty() {
        return this.document !== null && this.draft !== this.initial;
    }
    get hasUnsavedWork() {
        return this.dirty || this.comparisonDraft !== null || this.uncertainSave;
    }
    get shouldWarnBeforeUnload() {
        return this.hasUnsavedWork || this.saving !== null;
    }
    setIdentity(userId: string | null, { discard = false }: {
        discard?: boolean;
    } = {}) {
        this.epoch++;
        this.selection++;
        if (this.saving)
            this.uncertainSave = true;
        this.saving = null;
        if (userId && this.owner && userId !== this.owner && this.hasUnsavedWork && !discard) {
            this.identity = null;
            this.paused = true;
            return false;
        }
        if (discard || (userId && this.owner && userId !== this.owner))
            this.clear();
        this.identity = userId;
        this.paused = userId === null;
        return true;
    }
    hideDocument() {
        this.epoch++; this.selection++; this.saving = null; this.clear();
    }
    replaceDocument(snapshot: Document) { this.apply(snapshot); }
    acceptOperation(snapshot: Document, localContent: string) {
        this.apply(snapshot);
        this.draft = normalize(localContent);
    }
    private clear() {
        this.document = null;
        this.owner = null;
        this.original = '';
        this.initial = '';
        this.draft = '';
        this.revision = null;
        this.comparisonDraft = null;
        this.latest = null;
        this.conflict = false;
        this.uncertainSave = false;
    }
    open(snapshot: Document) {
        if (this.paused || !this.identity || this.hasUnsavedWork || this.saving)
            return false;
        this.apply(snapshot);
        return true;
    }
    private apply(snapshot: Document) {
        this.generation++;
        this.owner = this.identity;
        this.document = snapshot;
        this.original = snapshot.content;
        this.initial = normalize(snapshot.content);
        this.draft = this.initial;
        this.revision = snapshot.revision_id;
        this.saving = null;
        this.conflict = false;
        this.uncertainSave = false;
        this.latest = null;
        this.comparisonDraft = null;
    }
    edit(text: string) {
        if (!this.paused && this.document && this.owner === this.identity)
            this.draft = normalize(text);
    }
    private ticket(): Ticket {
        return { id: ++this.sequence, epoch: this.epoch, userId: this.identity, selection: this.selection, documentGeneration: this.generation, objectId: this.document?.id ?? null };
    }
    private current(ticket: Ticket) {
        return !this.paused && ticket.epoch === this.epoch && ticket.userId === this.identity && ticket.selection === this.selection && ticket.documentGeneration === this.generation && ticket.objectId === (this.document?.id ?? null);
    }
    beginLoad() {
        if (!this.hasUnsavedWork && !this.saving)
            this.selection++;
        return this.ticket();
    }
    finishLoad(ticket: Ticket, snapshot: Document) {
        if (!this.current(ticket))
            return false;
        return this.open(snapshot);
    }
    beginSave(): SaveTicket | null {
        if (this.paused || this.owner !== this.identity || !this.document || !this.revision || (!this.dirty && !this.uncertainSave) || this.saving || this.conflict)
            return null;
        this.saving = { ...this.ticket(), object_id: this.document.id, content: this.draft, expected_revision_id: this.revision };
        return this.saving;
    }
    private saveCurrent(ticket: SaveTicket) {
        return this.saving?.id === ticket.id && this.current(ticket);
    }
    saveSucceeded(ticket: SaveTicket, snapshot: Document) {
        if (!this.saveCurrent(ticket) || snapshot.id !== ticket.object_id)
            return false;
        this.saving = null;
        this.uncertainSave = false;
        this.document = snapshot;
        this.original = snapshot.content;
        this.initial = normalize(snapshot.content);
        this.revision = snapshot.revision_id;
        this.conflict = false;
        this.latest = null;
        this.latestId = 0;
        if (!this.dirty)
            this.comparisonDraft = null;
        return true;
    }
    saveFailed(ticket: SaveTicket, code: string) {
        if (!this.saveCurrent(ticket))
            return false;
        this.saving = null;
        if (['network', 'response', 'unauthenticated'].includes(code))
            this.uncertainSave = true;
        this.conflict = code === 'conflict';
        return true;
    }
    beginLatest() {
        const ticket = this.ticket();
        this.latestId = ticket.id;
        return ticket;
    }
    private latestCurrent(ticket: Ticket) {
        return this.current(ticket) && ticket.id === this.latestId;
    }
    setLatest(snapshot: Document, epoch = this.epoch, ticket?: Ticket) {
        if (epoch !== this.epoch || this.paused || snapshot.id !== this.document?.id || (ticket && !this.latestCurrent(ticket)))
            return false;
        this.latest = snapshot;
        return true;
    }
    startMerge(ticket?: Ticket) {
        if (this.paused || this.saving || !this.latest || (ticket && !this.latestCurrent(ticket)))
            return false;
        const draft = this.draft;
        this.apply(this.latest);
        this.comparisonDraft = draft;
        return true;
    }
}
