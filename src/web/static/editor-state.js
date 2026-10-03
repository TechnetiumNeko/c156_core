// 纯编辑状态：不访问 DOM，不发 HTTP 请求。app.js 用保存/加载结果推进状态。
const textareaText = (value) => value.replace(/\r\n?/g, '\n');

export class EditorState {
  constructor() {
    this.identity = null;
    this.owner = null;
    this.epoch = 0;
    this.paused = true;
    this.uncertainSave = false;
    this.document = null;
    this.original = ''; // 服务返回的原文，可能含 CRLF
    this.initial = ''; // 已保存正文在 textarea 中的呈现值
    this.draft = ''; // 用户当前输入
    this.revision = null; // 当前草稿基于的正文修订
    this.loading = 0; // 加载序号：后发的请求作废前一个请求
    this.saving = null; // 在途保存请求，含发送时的正文与修订
    this.conflict = false;
    this.latest = null;
    this.comparisonDraft = null; // 手动合并时保留的旧草稿
  }

  get dirty() {
    return !!this.document && this.draft !== this.initial;
  }

  get hasUnsavedWork() {
    return this.dirty || this.comparisonDraft !== null || this.uncertainSave;
  }

  get shouldWarnBeforeUnload() {
    return this.hasUnsavedWork || !!this.saving;
  }

  setIdentity(userId, {discard = false} = {}) {
    this.epoch += 1;
    this.loading += 1;
    if (this.saving) this.uncertainSave = true;
    this.saving = null;
    if (userId && this.owner && userId !== this.owner && this.hasUnsavedWork && !discard) {
      this.identity = null;
      this.paused = true;
      return false;
    }
    if (discard || (userId && this.owner && userId !== this.owner)) {
      this.document = null; this.original = ''; this.initial = ''; this.draft = '';
      this.revision = null; this.comparisonDraft = null; this.latest = null;
      this.conflict = false; this.uncertainSave = false; this.owner = null;
    }
    this.identity = userId;
    this.paused = !userId;
    return true;
  }

  open(snapshot) {
    this.owner = this.identity;
    this.uncertainSave = false;
    this.document = snapshot;
    this.original = snapshot.content;
    this.initial = textareaText(snapshot.content);
    this.draft = this.initial;
    this.revision = snapshot.revision_id;
    this.saving = null;
    this.conflict = false;
    this.latest = null;
    this.comparisonDraft = null;
  }

  edit(value) {
    this.draft = value;
  }

  beginLoad() {
    return {sequence: ++this.loading, epoch: this.epoch, userId: this.identity};
  }

  finishLoad(sequence, snapshot) {
    if (sequence.sequence !== this.loading || sequence.epoch !== this.epoch || sequence.userId !== this.identity) return false;
    this.open(snapshot);
    return true;
  }

  beginSave() {
    if (this.paused || (!this.dirty && !this.uncertainSave) || this.saving || this.conflict) return null;
    // 捕获发送时的内容；之后的输入继续留在 draft，不能被响应覆盖。
    this.saving = {
      epoch: this.epoch,
      userId: this.identity,
      object_id: this.document.id,
      content: this.draft,
      expected_revision_id: this.revision,
    };
    return this.saving;
  }

  saveSucceeded(request, snapshot) {
    if (this.saving !== request || request.epoch !== this.epoch || request.userId !== this.identity || this.document?.id !== request.object_id) {
      return false;
    }
    this.saving = null;
    this.uncertainSave = false;
    this.document = snapshot;
    this.original = snapshot.content;
    this.initial = textareaText(snapshot.content);
    this.revision = snapshot.revision_id;
    this.conflict = false;
    // 不给 draft 赋值：保存期间可能已有新输入。
    if (!this.dirty) this.comparisonDraft = null;
    return true;
  }

  saveFailed(request, code) {
    if (this.saving !== request) return false;
    this.saving = null;
    if(['network','response','unauthenticated'].includes(code))this.uncertainSave=true;
    this.conflict = code === 'conflict';
    // 失败不改变正文或基础修订，重试仍基于原来的版本。
    return true;
  }

  setLatest(snapshot, epoch = this.epoch) {
    if (epoch === this.epoch && !this.paused && snapshot.id === this.document?.id) { this.latest = snapshot; return true; }
    return false;
  }

  startMerge() {
    if (!this.latest) return false;
    const oldDraft = this.draft;
    this.open(this.latest);
    this.comparisonDraft = oldDraft;
    return true;
  }
}
