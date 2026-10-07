export interface Node { id: string; kind: string; name: string; parent_id: string | null; position: number; version: number; path: string; created_at: string; modified_at: string; metadata: unknown }
export interface Access { version: number; actions: string[]; visibility: string; frozen: boolean; can_freeze: boolean; can_unfreeze: boolean }
export interface Document extends Node { content: string; revision_id: string }
export interface User { id: string; login_name: string; display_name: string; status: string; site_admin: boolean; version: number }
export interface Session { user: User; csrf: string; expires_at: string }
export type Bootstrap = { initialized: boolean; nonce: string } | (Session & { initialized: boolean; workspace_access_version: number; workspace_role: string | null; root: Node | null; root_access: Access | null });
export interface DocumentResponse { document: Document; access: Access }
export interface ChildrenResponse { nodes: (Node & { access: Access })[] }
export interface SaveDocument { object_id: string; content: string; expected_revision_id: string }
