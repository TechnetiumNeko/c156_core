import { ApiClient, ApiError } from '../api/client.ts';
import type { Bootstrap, Node, Access, User } from '../api/types.ts';
import { EditorState } from './editor.ts';
export interface SessionLifecycle { beforeLeaveIdentity(): Promise<void>; retainedIdentity?(): string | null }
export class SessionState {
    scope: { workspace_id: string; branch_id: string } | null = null;
    private lifecycle?: SessionLifecycle;
    user: User | null = null;
    workspaceRole: string | null = null;
    root: Node | null = null;
    rootAccess: Access | null = null;
    initialized = false;
    blocked = false;
    recoveryNeeded = true;
    epoch = 0;
    private pending: Bootstrap | null = null;
    private client: ApiClient;
    private editor: EditorState;
    constructor(client: ApiClient, editor: EditorState, lifecycle?: SessionLifecycle) {
        this.lifecycle = lifecycle;
        this.client = client;
        this.editor = editor;
    }
    private reset() {
        this.scope = null;
        this.user = null;
        this.workspaceRole = null;
        this.root = null;
        this.rootAccess = null;
        this.pending = null;
        this.blocked = false;
    }
    private accept(value: Bootstrap, discard = false) {
        this.recoveryNeeded = false;
        this.initialized = value.initialized;
        if ('nonce' in value) {
            this.reset();
            this.editor.setIdentity(null, { discard });
            return true;
        }
        if (!this.editor.setIdentity(value.user.id, { discard })) {
            this.reset();
            this.blocked = true;
            this.pending = value;
            return false;
        }
        this.scope = value.scope;
        this.user = value.user;
        this.workspaceRole = value.workspace_role;
        this.root = value.root;
        this.rootAccess = value.root_access;
        this.blocked = false;
        this.pending = null;
        return true;
    }
    private async acceptBootstrap(value: Bootstrap, epoch: number, discard = false) {
        if (epoch !== this.epoch) return false;
        const owner = this.lifecycle?.retainedIdentity?.() ?? this.editor.owner ?? this.editor.identity;
        if (!('nonce' in value) && owner && owner !== value.user.id) {
            // Pause old callbacks before draining; do not accept a foreign account while old memory remains.
            this.reset();
            this.editor.setIdentity(null);
            try { await this.lifecycle?.beforeLeaveIdentity(); }
            catch (error) {
                if (epoch !== this.epoch) return false;
                this.blocked = true; this.pending = value;
                throw error;
            }
            if (epoch !== this.epoch) return false;
        }
        return this.accept(value, discard);
    }
    async acceptPending({ discard = false }: {
        discard?: boolean;
    } = {}) {
        if (!this.pending || !discard)
            return false;
        return this.acceptBootstrap(this.pending, this.epoch, true);
    }
    private begin() {
        this.recoveryNeeded = true;
        const nonce = this.client.nonce;
        const csrf = this.client.csrf;
        this.client.invalidate();
        this.client.nonce = nonce;
        this.client.csrf = csrf;
        return ++this.epoch;
    }
    private async anonymousBootstrap() {
        try {
            return await this.client.bootstrap();
        }
        catch (error) {
            if (!(error instanceof ApiError) || error.status !== 401)
                throw error;
            return this.client.bootstrap();
        }
    }
    async bootstrap() {
        const epoch = this.begin();
        const value = await this.anonymousBootstrap();
        if (epoch !== this.epoch)
            return false;
        return this.acceptBootstrap(value, epoch);
    }
    async login(loginName: string, password: string) {
        await this.lifecycle?.beforeLeaveIdentity();
        const epoch = this.begin();
        this.reset();
        this.editor.setIdentity(null);
        try {
            await this.client.login(loginName, password);
        }
        catch (error) {
            if (epoch !== this.epoch)
                return false;
            if (error instanceof ApiError && error.status === 401) {
                try {
                    const anonymous = await this.anonymousBootstrap();
                    if (epoch !== this.epoch)
                        return false;
                    await this.acceptBootstrap(anonymous, epoch);
                }
                catch {
                    // Report the original authentication error even if nonce recovery fails.
                    if (epoch !== this.epoch)
                        return false;
                }
            }
            throw error;
        }
        if (epoch !== this.epoch)
            return false;
        const value = await this.client.bootstrap();
        if (epoch !== this.epoch)
            return false;
        return this.acceptBootstrap(value, epoch);
    }
    async logout() {
        await this.lifecycle?.beforeLeaveIdentity();
        const epoch = this.begin();
        try {
            await this.client.logout();
        }
        catch (error) {
            if (epoch !== this.epoch)
                return false;
            if (!(error instanceof ApiError) || error.status !== 401)
                throw error;
        }
        if (epoch !== this.epoch)
            return false;
        this.reset();
        this.editor.setIdentity(null, { discard: true });
        this.client.invalidate();
        const value = await this.anonymousBootstrap();
        if (epoch !== this.epoch)
            return false;
        return this.acceptBootstrap(value, epoch);
    }
    async expire() {
        const epoch = this.begin();
        this.reset();
        this.editor.setIdentity(null);
        this.client.invalidate();
        const value = await this.anonymousBootstrap();
        if (epoch !== this.epoch)
            return false;
        return this.acceptBootstrap(value, epoch);
    }
}
