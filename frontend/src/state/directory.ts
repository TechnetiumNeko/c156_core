import { ApiClient } from '../api/client.ts';
import type { Node, ChildrenResponse } from '../api/types.ts';
export class DirectoryState {
  root: Node | null = null;
  userId: string | null = null;
  children: Record<string, ChildrenResponse['nodes']> = {};
  expanded: Record<string, boolean> = {};
  loading: Record<string, boolean> = {};
  errors: Record<string, string> = {};
  private generation = 0;
  private client: ApiClient;
  constructor(client: ApiClient) { this.client = client; }
  reset() { this.generation++; this.root = null; this.userId = null; this.children = {}; this.expanded = {}; this.loading = {}; this.errors = {}; }
  setRoot(root: Node | null, userId: string | null) { this.reset(); this.root = root; this.userId = userId; }
  async loadChildren(folderId: string) {
    if (!this.userId || this.loading[folderId]) return;
    const generation = this.generation;
    this.loading[folderId] = true;
    delete this.errors[folderId];
    try {
      const value = await this.client.listChildren(folderId);
      if (generation === this.generation) this.children[folderId] = value.nodes;
    } catch (error) {
      if (generation !== this.generation) return;
      this.errors[folderId] = error instanceof Error ? error.message : '目录读取失败';
      throw error;
    } finally { if (generation === this.generation) this.loading[folderId] = false; }
  }
  async toggle(folderId: string) {
    this.expanded[folderId] = !this.expanded[folderId];
    if (this.expanded[folderId] && !this.children[folderId]) await this.loadChildren(folderId);
  }
}
