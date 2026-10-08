export interface Node { id: string; kind: string; name: string; parent_id: string | null; position: number; version: number; path: string; created_at: string; modified_at: string; metadata: unknown }
export interface Access { version: number; actions: string[]; visibility: string; frozen: boolean; can_freeze: boolean; can_unfreeze: boolean }
export interface Document extends Node { content: string; revision_id: string }
export interface User { id: string; login_name: string; display_name: string; status: string; site_admin: boolean; version: number }
export interface Session { user: User; csrf: string; expires_at: string }
export type Bootstrap = { initialized: boolean; nonce: string } | (Session & { initialized: boolean; scope: { workspace_id: string; branch_id: string }; workspace_access_version: number; workspace_role: string | null; root: Node | null; root_access: Access | null });
export interface DocumentResponse { document: Document; access: Access }
export interface ChildrenResponse { nodes: (Node & { access: Access })[] }
export interface SaveDocument { object_id: string; content: string; expected_revision_id: string }
export interface UserResponse { user: User }
export interface AccountGrant extends UserResponse { token: string; purpose: string; expires_at: string }
export interface Workspace { version: number; read_scope: string; members: { user: User; role: string; status: string }[] }
export interface WorkspaceResponse { workspace: Workspace }
export interface NodeResponse { node: Node; access?: Access }
export interface DeletePlan { object_id: string; version: number; subtree_token: string; items: { node: Node; depth: number }[] }
export type UserAction = 'activation' | 'reset' | 'disable' | 'enable';

export interface WorkspaceInvitationResponse extends AccountGrant { workspace: Workspace }

export type SaveDocumentOperation = SaveDocument & { operation_id: string };
export interface RestoreDocumentOperation { object_id: string; source_revision_id: string; expected_revision_id: string; operation_id: string }
export interface OperationReceipt { operation_id: string; operation_type: 'save' | 'restore'; result_revision_id: string; changed: boolean; created_at: string }
export interface OperationResult { operation: OperationReceipt; current_revision_id: string }
export type OperationStatus = OperationResult | { operation: null };
export interface RevisionSummary { revision_id: string; parent_revision_id: string | null; actor_id: string | null; actor_display_name: string | null; source_kind: 'save' | 'restore' | 'import' | 'unknown'; restored_from_revision_id: string | null; created_at: string }
export interface RevisionView extends RevisionSummary { content: string }
export interface RevisionPage { revisions: RevisionSummary[]; head_revision_id: string; next_cursor: string | null }
export interface RevisionDiff { from_revision_id: string; to_revision_id: string; diff: string }
export interface DeletedDocumentSummary { object_id: string; name: string; path: string }
export interface DeletedDocumentPage { documents: DeletedDocumentSummary[]; next_cursor: string | null }
