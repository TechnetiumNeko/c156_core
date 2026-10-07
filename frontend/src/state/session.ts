import { ApiClient, ApiError } from '../api/client.ts';
import type { Bootstrap, Node, Access, User } from '../api/types.ts';
import { EditorState } from './editor.ts';
export class SessionState {
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
    constructor(client: ApiClient, editor: EditorState) {
        this.client = client;
        this.editor = editor;
    }
    private reset() {
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
        this.user = value.user;
        this.workspaceRole = value.workspace_role;
        this.root = value.root;
        this.rootAccess = value.root_access;
        this.blocked = false;
        this.pending = null;
        return true;
    }
    acceptPending({ discard = false }: {
        discard?: boolean;
    } = {}) {
        if (!this.pending || !discard)
            return false;
        return this.accept(this.pending, true);
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
        return this.accept(value);
    }
    async login(loginName: string, password: string) {
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
                    this.accept(anonymous);
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
        return this.accept(value);
    }
    async logout() {
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
        this.editor.setIdentity(null);
        this.client.invalidate();
        const value = await this.anonymousBootstrap();
        if (epoch !== this.epoch)
            return false;
        return this.accept(value);
    }
    async expire() {
        const epoch = this.begin();
        this.reset();
        this.editor.setIdentity(null);
        this.client.invalidate();
        const value = await this.anonymousBootstrap();
        if (epoch !== this.epoch)
            return false;
        return this.accept(value);
    }
}
