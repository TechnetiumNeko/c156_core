export interface Node { id: string; kind: string; name: string; parent_id: string | null; position: number; version: number; path: string; created_at: string; modified_at: string; metadata: unknown }
export interface Access { version: number; actions: string[]; visibility: string; frozen: boolean; can_freeze: boolean; can_unfreeze: boolean }
export interface Document extends Node { content: string; revision_id: string }
export interface User { id: string; login_name: string; display_name: string; status: string; site_admin: boolean; version: number }
export interface Session { user: User; csrf: string; expires_at: string }
export type Bootstrap = { initialized: boolean; nonce: string } | (Session & { initialized: boolean; workspace_access_version: number; workspace_role: string | null; root: Node | null; root_access: Access | null });
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
