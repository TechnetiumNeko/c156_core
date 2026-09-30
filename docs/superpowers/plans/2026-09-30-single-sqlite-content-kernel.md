# 单 SQLite 虚拟文件系统与共用内容内核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将旧单对象 SQLite 协议迁移为统一 SQLite 虚拟文件系统，通过共用 ContentService 完成内容操作并接入现有 CLI 和终端编辑器。

**Architecture:** 对象身份、分支 entry 和不可变正文修订分别存储。ContentService 决定事务和业务规则，Repository 执行 SQL，Database 管理连接；入口只持有会话及纯数据。旧容器只在离线迁移中读取，发布和 WAL 配置分别提供失败恢复。

**Tech Stack:** Python、标准库 sqlite3／unittest／argparse／dataclasses；现有 readline 和终端编辑器。当前环境为 Python 3.13.2；本阶段不增加第三方依赖，不要求 SQLite JSON 扩展。

**Spec:** [2026-09-30-single-sqlite-content-kernel-design.md](../specs/2026-09-30-single-sqlite-content-kernel-design.md)。实施前同时阅读 spec 和本计划；spec 的业务契约优先。

## Global Constraints

- “数据库协议版本使用 `PRAGMA user_version`；第一版为 1。”
- “写操作使用短 `BEGIN IMMEDIATE` 事务”；只读操作使用读事务，交互期间不保留连接或事务。
- “连接创建时启用外键并配置 5 秒 busy timeout”；正式运行库使用 WAL，业务入口不自动建库、建表、导入或修复。
- “DAO 方法不自行 begin、commit、rollback、重连或执行 schema 初始化，也不调用 ContentService。”服务不直接写 SQL。
- “路径解析及按 ID 操作均检查对象属于 scope 的活动子树，不能通过直接传 ID 绕过访问根。”
- “正文修订为后续恢复提供数据基础，但历史浏览、正文恢复、软删除恢复及作品级回退接口均不在本阶段交付。”
- “迁移测试使用复制的旧数据，不修改仓库中的样本。”样本为 7 个文件夹、1 篇文档，文档 ID 为 `8b168849-1ab3-4542-9828-f2e2120f2d57`。
- “本阶段保留现有 CLI 命令集合。”新核心操作通过 Python API 验证，不增加浏览器交互测试或真实终端自动交互。
- 不实现分支创建／切换、提交图、权限、实时协作、媒体上传、资源回收或插件存储框架；root_id 不是授权凭据。
- 代码实施时先用 using-git-worktrees 确认隔离工作区；本计划编写阶段不迁移数据、不改产品代码、不自动提交用户已有 spec 修改。
- 若选择子 agent：每个任务 prompt 必须携带绝对 cwd、该任务文件所有权、上下游接口、上述全局约束和验收命令；不得部署、推送、读取密钥或再委派。告知执行者并非独占代码库，不得回退他人修改。任务交接与 follow-up 保留这些约束；共享文件的任务顺序执行。

## Review Focus

1. 缺失／空／未来版本数据库及非 WAL 目标：启动不能悄悄创建或改写；Tasks 2、4、10 验证文件和 schema 均不变。
2. 包含空格、引号、连续斜杠及文档后接 `..` 的虚拟路径：词法处理不能绕过类型和范围检查；Tasks 1、4、10 验证解析和可用的补全输出。
3. 子树快照读取后深层正文修改或节点移走：旧 token 不能删除并发成果；Task 7 使用第二连接证明。
4. 源／目标位置重叠、符号链接、遗留 journal 及发布后中断：扫描不能遗漏或越界，失败不能覆盖目标；Tasks 8、9 验证。
5. 编辑器无操作保存 CRLF 正文，以及保存冲突后继续编辑：前者不意外改正文，后者不丢 buffer 或刷新基础修订；Task 11 使用替代编辑器验证。

## 文件与接口约定

| 文件 | 所有权与责任 |
| --- | --- |
| `src/core/{__init__,models,errors,paths,json_values}.py` | Task 1：纯数据、错误、词法及严格 JSON 辅助；不访问存储或入口 |
| `src/storage/{__init__,schema,database,errors}.py` | Task 2：协议、约束、连接与事务；管理初始化与运行打开明确分开 |
| `src/storage/{records,repository}.py` | Task 3：数据库记录和参数化 SQL；后续写操作只增加这里的必要 DAO 方法 |
| `src/storage/management.py` | Task 4：init 与默认树验证；Task 9：复用运行配置完成迁移后的 WAL 就绪 |
| `src/services/{__init__,content}.py` | Tasks 4–7：ContentService；顺序追加方法，不另设转发 DAO 层 |
| `src/storage/legacy.py` | Task 8：只读扫描、验证、顺序修复、指纹与报告 |
| `src/storage/{publication,__main__}.py` | Task 9：原子不覆盖发布、迁移协调、argparse 管理入口 |
| `src/cli/{paths,app,commands,completion}.py` | Task 10：查询与会话；Task 11：mkdir／edit 及失败 buffer；共享文件顺序执行 |
| `src/editor/{editor,__init__}.py` | Task 11：接收纯编辑输入，保持终端交互职责 |
| `src/file/*`、README、相关文档、`.gitignore` | Tasks 9、12：忽略运行产物；移除旧运行接口并更新文档 |
| `tests/__init__.py`、`tests/helpers.py`、`tests/test_*.py` | 对应任务建立行为测试；helpers 仅放临时库、样本复制、fixture 和计数工具 |

Task 1 的模型固定为 frozen dataclass：`ContentScope(workspace_id, branch_id, root_id)`；`NodeSnapshot(id, kind, name, parent_id, position, version, path, created_at, modified_at, deleted_at, metadata)`；`DocumentSnapshot` 继承 NodeSnapshot 并增加 `content, revision_id`；`TreeItem(node, depth)`；`DeleteSnapshot(object_id, version, items, subtree_token)`。TreeItem 列表按先序、兄弟 position 输出；快照集合使用 tuple，metadata 深层冻结为只读映射／tuple，get_metadata 返回解冻后的独立 dict。

所有 ContentService 方法是实例方法，公开签名遵循 spec 4.2，参数 scope 必须显式传入。初始化入口 `initialize_database(database: Path) -> ContentScope` 返回工作区根 scope；`ContentService.default_scope() -> ContentScope` 返回 CLI main 根 scope。这些是不同访问根，测试不得混用。

错误继承 `ContentError(Exception)`，提供稳定 `code` 和结构化 `details`，不包含终端／HTTP 输出。NotFound 用于缺失或已删除；PathOutsideRoot 用于存在但超出访问根；输入版本不匹配用 Conflict。跨工作区／分支按该 scope 查询不到对象时返回 NotFound，不能回退到全局 ID 查询。

## 实施顺序与交付点

严格依赖链：1 → 2 → 3 → 4 → 5 → 6 → 7；8 在 4 后可开展，9 依赖 5、8；10 依赖 4、9，11 依赖 5、10；12 汇总全部。默认顺序执行，不主动并行修改共享模块。

交付点 A（Task 5）：新库可通过服务初始化、浏览、创建和保存。交付点 B（Task 9）：旧数据可安全导入。交付点 C（Task 12）：CLI／编辑器完成切换，旧运行存储接口退出。每项任务完成红绿验证后只提交该任务文件，不使用 `git add .`；下文提交信息可直接使用，部署／推送不属于计划。

---

### Task 1：建立纯模型、领域错误与虚拟路径词法

**Files:** Create `src/core/__init__.py`、`models.py`、`errors.py`、`paths.py`、`json_values.py`；Create `tests/__init__.py`、`tests/test_core.py`。

**Interfaces:**
- Produces：上文全部模型和 spec 4.4 的错误类；`ParsedPath(absolute: bool, parts: tuple[str, ...], trailing_slash: bool)`。
- Produces：`parse_path(value: str) -> ParsedPath`、`validate_name(name: str) -> None`、`validate_metadata(value: dict, *, reject_reserved: bool = True) -> None`、`freeze_json(value)`、`thaw_json(value)`、`json_equal(left, right) -> bool`。
- Consumes：标准库；不导入 storage／services／CLI／editor。

- [ ] **Step 1：写失败测试。** `test_name_rules` 允许中文和空格，拒绝空名、`.`、`..`、斜杠、反斜杠、NUL；`test_parse_keeps_dotdot` 断言 `parse_path('文档/../设定').parts == ('文档', '..', '设定')`。覆盖 `/`、`~`、`~/`、`~name`、连续 `/` 和空路径。`test_metadata_strict_and_detached` 拒绝嵌套非字符串键、NaN、Infinity、结构字段；None 合法，布尔值不等于 1；冻结后不能修改嵌套值，解冻副本不改变快照。

  ```python
  def test_parse_keeps_dotdot(self):
      self.assertEqual(parse_path('文档/../设定').parts, ('文档', '..', '设定'))
      self.assertFalse(json_equal(True, 1))
  ```

- [ ] **Step 2：确认红。** Run `python -m unittest tests.test_core -v`；新接口尚不存在导致失败，不能是语法错误。
- [ ] **Step 3：实现上述签名和 frozen 模型。** 词法仅识别根前缀、拆段和 trailing_slash；保留 `.`／`..` 供服务逐段处理。metadata 验证显式接受 JSON 类型，不以 json.dumps 的自动转换作为合法性依据；保留键集合与 spec 一致。
- [ ] **Step 4：确认绿。** Run `python -m unittest tests.test_core -v`；全部通过，纯模块 import 不打开数据库。
- [ ] **Step 5：提交任务文件。** Commit message：`feat: add pure content models and virtual path rules`。

### Task 2：实现 schema、连接与显式事务

**Files:** Create `src/storage/__init__.py`、`schema.py`、`database.py`、`errors.py`；Create `tests/helpers.py`、`tests/test_database.py`。

**Interfaces:**
- Produces：`create_schema(connection: sqlite3.Connection) -> None`，只接收管理入口的连接，不提交。
- Produces：`Database(path: Path, *, busy_timeout_ms: int = 5000)`；`Database.transaction(*, write: bool = False)` 上下文返回 connection，退出关闭连接。
- Produces：`Database.management_connection()` 上下文用于已有库配置／验证（允许非 WAL，不建表）；`Database.configure_runtime() -> None` 验证后设置 WAL，仅管理入口调用。建库由管理代码独占创建文件并连接，不由运行 Database 创建。
- Produces：存储错误 `StorageError`、`BusyError`、`ConstraintError`、`SchemaError`；SchemaError 用于协议／运行配置不兼容，已知约束带约束类别和原始异常 cause。

- [ ] **Step 1：写失败测试。** `test_missing_runtime_database_is_not_created`；`test_empty_future_and_nonwal_database_unchanged`；`test_rollback_across_statements` 两次写入后抛异常，第二连接看到两次均撤销；`test_connections_enable_foreign_keys`。直接 SQL 验证兄弟同名／同 position、第二活动根、负 position、零 version、跨工作区／对象修订外键、修订 UPDATE／DELETE 均被拒绝；软删除后名称可复用。不以外键证明父类型和目录环，那是服务规则。

  ```python
  def test_missing_runtime_database_is_not_created(self):
      with self.assertRaises(StorageError):
          with Database(self.missing_path).transaction():
              self.fail('缺失数据库不能被打开')
      self.assertFalse(self.missing_path.exists())
  ```

- [ ] **Step 2：确认红。** Run `python -m unittest tests.test_database -v`。
- [ ] **Step 3：实现 schema 和连接边界。** 普通打开使用 SQLite URI `mode=rw`，不生成缺失文件；`isolation_level=None`，row_factory 为 sqlite3.Row，每连接 foreign_keys=ON 与 busy_timeout=5000。读用 BEGIN，写用 BEGIN IMMEDIATE；BEGIN、业务步骤、COMMIT 任一失败均关闭连接，若 in_transaction 为真则回滚，不掩盖原异常。
- [ ] **Step 4：完成约束和可插入顺序。** 表字段按 spec；objects UNIQUE(workspace_id,id)、branches UNIQUE(workspace_id,id)、revisions UNIQUE(workspace_id,object_id,id)。entries 外键指向这些键及自身 `(workspace_id,branch_id,parent_id)`；branches.root_object_id 引用同工作区 objects，根 entry 的匹配由服务验证，避免强制循环插入。活动兄弟索引以 workspace／branch／parent 为前缀，根独立唯一索引；增加 kind CHECK、整数类型与取值 CHECK、身份不可变触发器、修订不可变触发器。建表、user_version=1 必须在管理事务中一起成功，不使用会提前提交的 executescript 打断事务。
- [ ] **Step 5：确认绿并提交。** Run `python -m unittest tests.test_core tests.test_database -v`；检查创建失败无协议半成品。Commit message：`feat: add unified sqlite schema and transaction boundaries`。

### Task 3：实现 Repository 记录、范围查询和条件写入

**Files:** Create `src/storage/records.py`、`repository.py`、`tests/test_repository.py`；Modify `tests/helpers.py`。

**Interfaces:**
- Produces：`EntryRecord(workspace_id, branch_id, object_id, kind, parent_id, name, position, version, current_revision_id, metadata_json, created_at, modified_at, deleted_at)`、`RevisionRecord(id, workspace_id, object_id, parent_revision_id, content, created_at)`。
- Produces：`Repository(connection, *, workspace_id: str, branch_id: str)`；`get_entry(object_id) -> EntryRecord | None`、`get_branch_root_id() -> str | None`、`find_child(parent_id,name) -> EntryRecord | None`、`list_children(parent_id) -> list[EntryRecord]`、`ancestors(object_id) -> list[EntryRecord]`（自身到根）、`subtree(object_id) -> list[EntryRecord]`（活动先序）、`get_revision(object_id,revision_id) -> RevisionRecord | None`。
- Produces：`insert_object(object_id,kind,created_at)`、`insert_entry(record)`、`insert_revision(record)`；`update_entry(object_id, changes: dict, *, expected_version: int | None = None, expected_revision_id: str | None = None) -> int`；`touch_entries(object_ids: set[str], modified_at: str) -> int`、`next_position(parent_id) -> int`。
- Consumes：Task 2 connection，纯 records；不会产生 NodeSnapshot，不执行业务范围授权。

- [ ] **Step 1：写失败测试。** 建两个工作区／两分支 fixture；同 object 在不同分支有独立 entry，查询／条件更新不得串分支；修订按 workspace＋object 查。`test_conditional_update_rowcount` 成功为 1、过期为 0；`test_dao_does_not_commit` 在多个 DAO 写入后由外层抛异常，第二连接看到零新增。祖先查询有完整根链，子树排除 deleted 节点，列表按 position。

  ```python
  def test_conditional_update_rowcount(self):
      with self.database.transaction(write=True) as connection:
          repo = Repository(connection, workspace_id=self.scope.workspace_id,
                            branch_id=self.scope.branch_id)
          self.assertEqual(repo.update_entry(self.document_id,
              {'name': '改名', 'version': 2}, expected_version=1), 1)
          self.assertEqual(repo.update_entry(self.document_id,
              {'name': '过期改名'}, expected_version=1), 0)
  ```

- [ ] **Step 2：确认红。** Run `python -m unittest tests.test_repository -v`。
- [ ] **Step 3：实现接口。** SQL 全部参数化并限定 workspace／branch；update_entry 的 changes 使用内部固定白名单，禁止动态拼入任意列名。条件更新始终 `deleted_at IS NULL`；只在提供相应 expected 值时增加该条件。next_position 为活动子对象 MAX(position)+1。touch_entries 对去重 ID 每条 +1，在外层事务内运行。
- [ ] **Step 4：确认绿。** Run `python -m unittest tests.test_database tests.test_repository -v`。递归 SQL 对矛盾链有访问集合或终止保护，不能在坏数据上无限循环；DAO 不包含 BEGIN／COMMIT／ROLLBACK／connect。
- [ ] **Step 5：提交任务文件。** Commit message：`feat: add scoped sqlite repository and conditional writes`。

### Task 4：初始化默认树并实现服务读取与路径解析

**Files:** Create `src/storage/management.py`、`src/services/__init__.py`、`content.py`、`tests/test_initialization.py`、`tests/test_content_reads.py`；Modify `repository.py`、`tests/helpers.py`。

**Interfaces:**
- Produces：`initialize_database(database: Path) -> ContentScope`；`validate_default_tree(connection) -> ContentScope`（工作区根）；两者都是管理接口，不提供业务写入口。
- Produces：repository.py 的模块级管理写接口 `insert_workspace(connection, workspace_id, name, created_at) -> None`、`insert_branch(connection, workspace_id, branch_id, name, root_object_id, created_at) -> None`；必须使用传入管理事务连接，不提交。
- Produces：`ContentService(database: Database)`、`default_scope() -> ContentScope`；`get_node(scope,object_id)`、`get_path(scope,object_id)`、`resolve_path(scope,path,*,cwd_id=None)`、`list_children(scope,folder_id)`、`list_tree(scope,folder_id,*,max_depth=None)`、`get_metadata(scope,object_id)`，返回类型遵循 spec。
- Consumes：Tasks 1–3；在 repository.py 增加模块级 `lookup_default_main(connection: sqlite3.Connection) -> tuple[str,str]` 查询（workspace_id、branch_id），零个或多个工作区、main 缺失时失败，不任取第一条；服务传入本次读事务连接，管理入口传入管理连接。

- [ ] **Step 1：写失败测试。** `test_init_is_idempotent` 用合法 fixture 添加 entry 后再次 init，ID、顺序、时间不变；`test_default_scope_is_main` 工作区根 `/main` 在 CLI scope 下显示 `/`。覆盖 cwd 默认值、重命名后 ID 定位、scope 为文档／删除目录、直接 ID 越根、跨分支／工作区、NotDirectory、max_depth=0。路径包含 `missing/../x`、`document/../x`、`document/`、`//`、中文空格、根处 `..`；无效深度拒绝负数、bool 和非整数。

  ```python
  def test_document_dotdot_is_not_collapsed(self):
      with self.assertRaises(NotDirectory):
          self.service.resolve_path(self.scope, 'document/../x')
      self.assertEqual(len(self.service.list_tree(
          self.scope, self.scope.root_id, max_depth=0)), 1)
  ```

- [ ] **Step 2：确认红。** Run `python -m unittest tests.test_initialization tests.test_content_reads -v`。
- [ ] **Step 3：实现显式 init。** 新库独占创建，事务内建 schema 和默认树，objects→branches→root entry→四个顶层 entry，version=1，根 name=''、position=0，顶层按 main／admin／resource／bin 顺序。首次初始化失败只清理自身产物；完整提交后 configure_runtime，配置失败保留完整目标以便重跑。存在目标只验证协议、树并完成 WAL 配置；空／未知库拒绝覆盖。
- [ ] **Step 4：实现一致读取。** 每次 public 方法进入一个读事务，内部辅助接受同一 repo，禁止 public 方法互调另开连接。服务统一将 BusyError 映射 StorageBusy、SchemaError 映射 UnsupportedSchema；其他未知存储故障保留 cause 供诊断。范围校验验证 scope root 为活动文件夹、完整祖先最终到匹配分支根，目标链包含 root_id；路径在该事务内逐段处理，显示路径相对访问根生成。查询快照不修改 schema、版本或文件系统；default_scope 从分支根下定位受保护顶层 ID。
- [ ] **Step 5：确认绿并提交。** Run `python -m unittest tests.test_initialization tests.test_content_reads -v`；新增 `test_invalid_default_tree_not_repaired` 验证缺失默认目录及错误 root 匹配拒绝且无数据变化。Commit message：`feat: initialize content workspace and read virtual paths`。

### Task 5：实现创建、正文修订保存与扩展元数据

**Files:** Modify `src/services/content.py`、`src/storage/repository.py`、`tests/helpers.py`；Create `tests/test_content_writes.py`、`tests/test_content_concurrency.py`。

**Interfaces:**
- Produces：spec 的 `create_folder`、`create_document`、`read_document`、`save_document`、`set_metadata`；类型和关键字参数逐字沿用 spec。
- Consumes：范围辅助与 Repository 条件写；UUID 字符串，`datetime.now(timezone.utc).isoformat()`，每操作一个时间值。

- [ ] **Step 1：写失败测试。** 创建中文名、空正文首修订、追加 position 和父目录 version；正文新修订 parent 正确，旧修订不变。无变化保存不增修订／更新时间，但旧 expected_revision_id 即使正文相同也 Conflict。metadata 合并、None、嵌套副本、布尔与数值区别、保留字段和同值更新；不改变父目录版本。错误父类型／scope／名称无半成品。

  ```python
  def test_old_revision_conflicts_even_for_identical_content(self):
      before = self.service.read_document(self.scope, self.document_id)
      after = self.service.save_document(self.scope, before.id, '新正文',
                                        expected_revision_id=before.revision_id)
      with self.assertRaises(Conflict):
          self.service.save_document(self.scope, before.id, after.content,
                                    expected_revision_id=before.revision_id)
      self.assertEqual(self.service.read_document(self.scope, before.id), after)
  ```

- [ ] **Step 2：确认红。** Run `python -m unittest tests.test_content_writes tests.test_content_concurrency -v`。
- [ ] **Step 3：实现短写事务。** 输入纯校验在事务前；事务内重读当前对象、范围和类型。创建同时插入对象、entry、首修订并 touch 父目录；保存先比较修订，再比较正文，插修订后条件更新 entry。metadata expected_version 比较后才决定 no-op。构造返回快照在事务内，退出成功提交后才返回。
- [ ] **Step 4：验证中途失败和独立进程并发。** patch Repository.insert_entry／update_entry 在先前 DAO 写入后抛错，断言对象数、修订数、entry、时间和父版本全部回退。两个 multiprocessing `spawn` worker 用 Barrier 同名创建：一个成功，一个 AlreadyExists；两 worker 先读取同一 revision 再保存：一个成功，一个 Conflict，最终只有一条新增修订。设置队列／join 的有界超时防测试挂起，不用 sleep 判断先后。
- [ ] **Step 5：确认绿并提交。** Run `python -m unittest tests.test_content_writes tests.test_content_concurrency -v`。只将兄弟名称唯一约束映射 AlreadyExists；其他约束不能被吞掉。Commit message：`feat: add atomic content writes and revision conflicts`。

### Task 6：实现重命名和跨目录移动

**Files:** Modify `src/services/content.py`、`src/storage/repository.py`；Create `tests/test_content_moves.py`。

**Interfaces:** Produces spec `rename_node`、`move_node`；Consumes get_entry／ancestors／next_position／update_entry／touch_entries，预期版本只来自调用者读取值。

- [ ] **Step 1：写失败测试。** 移动目录后自身和后代 ID 不变、路径变化、正文修订不变；跨父追加 position，旧／新父各 +1；同父改名保留 position，父只 +1；同父同名和同名 rename 无变化。保护对象、自己／后代目标、目标文档、同名目标、过期版本、访问根外目标均失败。其他位置名叫 main 的目录允许修改。

  ```python
  def test_same_parent_move_is_noop(self):
      before = self.service.get_node(self.scope, self.document_id)
      after = self.service.move_node(self.scope, before.id, before.parent_id,
                                     expected_version=before.version)
      self.assertEqual(after, before)
  ```

- [ ] **Step 2：确认红。** Run `python -m unittest tests.test_content_moves -v`。
- [ ] **Step 3：实现事务步骤。** 在同一 BEGIN IMMEDIATE 查询源、目标及祖先，先验证版本、范围、保护和环，再判断 no-op；只更改 entry 结构，去重父 ID 后 touch。更新源失败回滚；父 touch 中途失败也整体撤销。
- [ ] **Step 4：确认绿。** Run `python -m unittest tests.test_content_reads tests.test_content_writes tests.test_content_moves -v`；`test_parent_update_failure_rolls_back_move` 断言结构、position 和两个父版本均保持原样。
- [ ] **Step 5：提交任务文件。** Commit message：`feat: add versioned virtual rename and move operations`。

### Task 7：实现删除快照 token 与原子软删除

**Files:** Modify `src/services/content.py`、`src/storage/repository.py`；Create `tests/test_content_deletes.py`；Modify `tests/test_content_concurrency.py`。

**Interfaces:** Produces `prepare_delete(scope,folder_id) -> DeleteSnapshot` 和 spec `delete_node(..., expected_subtree_token=None)`；Repository 增加 `soft_delete_entries(object_ids: set[str], deleted_at: str) -> int`（活动 entry +1 version、同一 modified_at／deleted_at）。

- [ ] **Step 1：写失败测试。** 空目录／文档非递归删除，非空目录 DirectoryNotEmpty；recursive 参数／token 契约、保护根、过期版本；成功后整个活动子树不可见但对象和正文修订仍存在，之前已删除后代版本／时间不变；父目录只 +1。删除中途抛异常时所有 entry 恢复。

  ```python
  def test_deep_save_invalidates_delete_snapshot(self):
      snapshot = self.service.prepare_delete(self.scope, self.folder_id)
      doc = self.other_service.read_document(self.scope, self.deep_document_id)
      self.other_service.save_document(self.scope, doc.id, '并发修改',
                                      expected_revision_id=doc.revision_id)
      with self.assertRaises(Conflict):
          self.service.delete_node(self.scope, self.folder_id,
              expected_version=snapshot.version, recursive=True,
              expected_subtree_token=snapshot.subtree_token)
      self.assertEqual(self.service.read_document(self.scope, doc.id).content, '并发修改')
  ```

- [ ] **Step 2：确认红。** Run `python -m unittest tests.test_content_deletes -v`。
- [ ] **Step 3：固定 token 算法并实现。** JSON payload 使用数组 `[workspace_id, branch_id, root_id, object_id, [[id,version],...]]`，节点按 id 的 BINARY 顺序；`json.dumps(..., ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')` 后 SHA-256 hexdigest。prepare_delete 在单读事务返回完整子树；删除在写事务内重新计算，比较目标版本和 token。token 是并发状态凭据，不替代范围／保护检查；不自动刷新或重试。
- [ ] **Step 4：用第二连接验证冲突。** 取得快照后另一 service 修改深层正文、metadata、新增、重命名、移出或删除节点，旧 token 全部 Conflict 且不产生额外删除；父目录仅因子树外其他兄弟改变时，目标自身和子树不变仍可删除。断言每个受影响 entry 最多 +1；soft_delete_entries 返回行数必须等于活动快照节点数，否则整次回滚。
- [ ] **Step 5：确认绿并提交。** Run `python -m unittest tests.test_content_deletes tests.test_content_concurrency -v`。Commit message：`feat: protect recursive soft deletion with subtree snapshots`。

### Task 8：建立旧容器只读扫描与结构化迁移报告

**Files:** Create `src/storage/legacy.py`、`tests/test_legacy_scan.py`；Modify `tests/helpers.py`。

**Interfaces:**
- Produces：frozen `LegacyObject(id, kind, relative_path, fullpath, parent_id, name, position, content, metadata, raw_metadata, created_at, modified_at)`、`LegacyScan(objects: tuple, source_digest: str, report: dict, imported_at: str)`。
- Produces：`scan_legacy(source: Path, *, target: Path, imported_at: str) -> LegacyScan`；`source_fingerprint(source: Path, *, target: Path) -> str`。报告是严格 JSON dict，逐对象含原 metadata／原时间／采用时间／正文散列，另外含修复项和 counts。
- Consumes：core 名称／JSON 规则、Task 4 默认树契约；不导入 src.file，不写源库。

- [ ] **Step 1：写失败测试。** 复制 data 到 TemporaryDirectory，扫描断言 8 个 ID、7 folder／1 document、正文 85 字符及 SHA-256 `0dd92b5440aa9d35c1da22ca232e346d57fd8fb800c712a6f05358969c93d268`、原 parent 关系和顺序。比较扫描前后全部源容器字节散列。删除文档登记允许报告修复并追加；悬空／错误登记、重复 ID、fullpath 矛盾、缺 .folder、损坏库、未知文件／表／类型、缺正文 singleton、异常 metadata 均中止。

  ```python
  def test_sample_scan_preserves_document(self):
      scan = scan_legacy(self.source_copy, target=self.target, imported_at=self.imported_at)
      self.assertEqual(len(scan.objects), 8)
      doc = next(item for item in scan.objects if item.kind == 'document')
      self.assertEqual(doc.id, '8b168849-1ab3-4542-9828-f2e2120f2d57')
      self.assertEqual(hashlib.sha256(doc.content.encode('utf-8')).hexdigest(),
          '0dd92b5440aa9d35c1da22ca232e346d57fd8fb800c712a6f05358969c93d268')
  ```

- [ ] **Step 2：确认红。** Run `python -m unittest tests.test_legacy_scan -v`。
- [ ] **Step 3：实现扫描与协议核对。** URI mode=ro，校验 sqlite_master 表及必要列、singleton 行和 file 引用；folder 允许 abstract_file／metadata／file，document 再有 document_content，文档 file 表必须为空。根 fullpath 为 /data，对每条物理相对路径推导 fullpath／父 ID。保留登记 position 的相对顺序，未登记按 name 的 BINARY 顺序追加并连续编号；根 name=''。源和 target 不得是同一源容器，拒绝扫描树中的符号链接及源 WAL／journal／SHM，不自动忽略未知内容。
- [ ] **Step 4：实现稳定指纹和时间映射。** 指纹对按相对路径排序的 `[path, sha256(bytes)]` 数组采用与 Task 7 相同 JSON 编码并散列，只排除明确目标及该目标命名空间内的临时产物。源目录中其他临时文件仍报错。统一使用传入 imported_at；时间仅接受带时区 ISO 并转 UTC，无时区／非法／缺失记录回退原因。正常扩展 metadata 完整保留，若键与结构保留字段冲突则报 MigrationError，不静默丢弃。
- [ ] **Step 5：确认绿并提交。** 增加无时区、非法时间、CRLF 正文、路径含空格和引号、源与目标重叠测试。Run `python -m unittest tests.test_legacy_scan -v`。Commit message：`feat: inspect legacy containers without modifying sources`。

### Task 9：实现原子导入、配置恢复和管理命令

**Files:** Create `src/storage/publication.py`、`src/storage/__main__.py`、`tests/test_legacy_import.py`、`tests/test_storage_commands.py`；Modify `src/storage/legacy.py`、`management.py`、`repository.py`、`.gitignore`。

**Interfaces:**
- Produces：legacy.py 中 `migrate_legacy(source: Path, database: Path) -> dict`（返回持久化报告）；publication.py 中 `publish_no_replace(temporary: Path, target: Path) -> None`；`src.storage.__main__.main(argv: list[str] | None = None) -> int`。
- Consumes：LegacyScan、create_schema、validate_default_tree、Database.configure_runtime、Task 4 的 insert_workspace／insert_branch，沿用 Repository 插入对象／entry／revision，不额外建批量框架。
- Produces：repository.py 的模块级 `get_import_report(connection, source_digest) -> dict | None`、`insert_import_report(connection, source_digest, imported_at, report: dict) -> None`、`verify_integrity(connection) -> None`（完整性／外键／目录可达性核对，失败抛存储错误）；全部使用传入事务连接，业务服务仍不写 SQL。

- [ ] **Step 1：写失败测试。** 导入后逐对象核对 ID、content、metadata、顺序、父关系、time 和 main scope 下 `/products/concretecream`。重复导入前先通过服务编辑，重跑后正文／revision 数不变；同来源返回原报告，不重新生成时间。不同来源、未知已有目标、两个迁移进程竞争目标都不能覆盖。源散列不变。

  ```python
  def test_repeat_import_preserves_later_edit(self):
      report = migrate_legacy(self.source_copy, self.target)
      service = ContentService(Database(self.target))
      scope = service.default_scope()
      doc = service.read_document(scope, '8b168849-1ab3-4542-9828-f2e2120f2d57')
      saved = service.save_document(scope, doc.id, '迁移后修改',
                                    expected_revision_id=doc.revision_id)
      self.assertEqual(migrate_legacy(self.source_copy, self.target), report)
      self.assertEqual(service.read_document(scope, doc.id), saved)
  ```

- [ ] **Step 2：确认红。** Run `python -m unittest tests.test_legacy_import tests.test_storage_commands -v`。
- [ ] **Step 3：实现离线协调。** 先扫描，再在目标目录独占创建以目标文件名开头的 `.migrate-<uuid>.tmp`，rollback journal、synchronous=FULL。一个事务建 schema，按 workspace→objects→branch→首 revisions→entries（父先于子）→legacy_imports 插入；保持原正文字符串。核对散列、metadata、顺序、integrity_check、foreign_key_check、可达性及源指纹；commit 关闭后 fsync 文件，使用 os.link 独占发布，再移除临时名并 fsync 父目录。任何 link 失败不得用覆盖 rename 降级。
- [ ] **Step 4：实现发布后恢复。** 不再通过 temporary 连接访问已发布 inode。目标发布后 configure_runtime 并验证再返回；此前失败仅清理自己的临时产物。发布后配置失败保留目标，MigrationError details 标注 `phase='configure_runtime'` 和重跑命令；重跑先匹配 source_digest，再验证现有库并补 WAL，不重新导入。缺少匹配记录或完整性错误拒绝恢复。测试 patch configure_runtime 抛错，以及 subprocess 在 os.link 后用 os._exit 退出；重跑成功且既有内容无变化。
- [ ] **Step 5：接入 argparse 并验证管理入口。** 支持 spec 的 `init --database` 和 `migrate-legacy --source --database`，错误非零退出且无成功输出。`.gitignore` 精确忽略运行库、sidecar 和迁移临时命名空间，不忽略旧 .folder／文档样本。子进程测试只在临时目录运行。Run `python -m unittest tests.test_legacy_import tests.test_storage_commands -v`；Commit message：`feat: publish legacy imports atomically and recover runtime setup`。

### Task 10：让 CLI 查询、会话、路径补全接入服务

**Files:** Modify `src/cli/app.py`、`paths.py`、`commands.py`、`completion.py`；Create `tests/test_cli_reads.py`、`tests/test_cli_completion.py`。

**Interfaces:**
- Produces：`VirtualFileSystem(service: ContentService, scope: ContentScope)`，字段 cwd_id；`resolve(value='.') -> NodeSnapshot`、`display(object_id=None) -> str`、`completion_candidates(value, directories_only=False) -> list[str]`、`ensure_cwd() -> bool`（重置时为 True）。
- Produces：`CLI(service: ContentService, scope: ContentScope, output=print, prompt=input)`；保留 execute／register 和命令注册结构；`main(argv: list[str] | None = None) -> int`、`default_database_path() -> Path`。
- Consumes：服务读接口和 default_scope；Path 只用于定位数据库，不能用于虚拟内容查询。

- [ ] **Step 1：写失败测试。** 两 CLI 独立 cwd；ls 文件、tree 默认深度 2／-d 0、cat 中文、cd／pwd、help／exit。移动 cwd 后路径变化，删除 cwd 后下一命令／prompt 提示并回 main；其他 CLI 不受影响。缺库／非 WAL／未知版本启动失败并给管理命令，不生成文件。

  ```python
  def test_cli_cwd_is_per_instance(self):
      first = CLI(self.service, self.scope, output=self.output)
      second = CLI(self.service, self.scope, output=self.output)
      first.execute('cd products')
      self.assertEqual(first.fs.display(), '/products')
      self.assertEqual(second.fs.display(), '/')
  ```

- [ ] **Step 2：确认红。** Run `python -m unittest tests.test_cli_reads tests.test_cli_completion -v`。
- [ ] **Step 3：替换宿主机路径。** commands 只读 NodeSnapshot.kind，树使用 TreeItem.depth 排版，ls 可保留目录优先和名称展示排序。启动 argparse `--database`，Database→ContentService→default_scope→CLI；删除 VirtualFileSystem 的 mkdir、iterdir 和物理 root。执行入口统一把 ContentError 转中文信息，未知存储异常不伪装为路径／同名错误。
- [ ] **Step 4：验证补全。** 补全仅调用 resolve_path／list_children，已删除／越界路径返回无候选；支持 `/`、`~/`、相对前缀、directories_only、tree 深度选项不补路径。对含空格、单／双引号的名称生成 shell 可接受的候选，使用 `shlex.split('cd ' + candidate)` 验证得到原路径；readline 未闭合引号处理保留上下文，不把拼接后命令解析为多个参数。用 mock readline 验证，不操作真实终端。
- [ ] **Step 5：确认绿并提交。** Run `python -m unittest tests.test_cli_reads tests.test_cli_completion -v`。mkdir／edit 的切换留给下一任务，此交付点仅声称查询命令已迁移。Commit message：`refactor: route cli browsing and completion through content service`。

### Task 11：接入 mkdir／edit 并保留失败编辑结果

**Files:** Modify `src/cli/commands.py`、`app.py`、`src/editor/editor.py`、`__init__.py`；Create `tests/test_cli_writes.py`、`tests/test_editor_contract.py`。

**Interfaces:**
- Produces：`Editor(document_id: str, title: str, content: str, system_command_handler=None)`，沿用 `run() -> EditorResult`；不接受 Document 容器或数据库连接。
- Produces：CLI 构造增加 keyword-only `editor_factory=Editor`；CommandContext 增加 `pending_edit: PendingEdit | None`，`PendingEdit(object_id, title, base_revision_id, content)` 是入口内存状态，不保存到数据库。
- Consumes：服务 create_folder／create_document／read_document／save_document；create 的 parent 由虚拟路径词法拆成父路径与叶名，再解析父目录，不把缺失路径传成宿主 Path。

- [ ] **Step 1：写失败测试。** mkdir 创建并登记；edit 缺文档确认 y／N、取消及新空文档保留；替代编辑器收到纯 ID／标题／正文，保存必须使用最初 revision_id。替代编辑器返回期间另一 service 修改正文，第一次保存 Conflict，结果仍在 pending_edit；StorageBusy／删除／移出访问根也保留结果，无静默覆盖。

  ```python
  def test_conflict_keeps_buffer_and_base(self):
      cli = CLI(self.service, self.scope, output=self.output, prompt=lambda _: 'later',
                editor_factory=self.conflicting_editor_factory)
      cli.execute('edit products/concretecream')
      self.assertEqual(cli.context.pending_edit.content, '用户未保存正文')
      self.assertEqual(cli.context.pending_edit.base_revision_id, self.original_revision_id)
  ```

- [ ] **Step 2：确认红。** Run `python -m unittest tests.test_cli_writes tests.test_editor_contract -v`。
- [ ] **Step 3：替换 Editor 输入与保存协调。** 移除 src.file import、isinstance(Document)、document.sql 等访问。CLI 读完文档退出读事务后才启动编辑器；返回后调用服务保存。失败保存 pending_edit，提示选择“继续编辑／查看待保存正文／稍后处理”，默认稍后处理；继续编辑保持旧 base_revision_id，不绑定最新修订。下一次 edit 同一对象可恢复 pending buffer；存在其他对象 pending 时先处理／明确放弃，再打开新对象。退出 CLI 有 pending 时提示并要求明确放弃，EOF 提示正文仍未保存。
- [ ] **Step 4：验证纯编辑与无操作保存。** 原正文含 CRLF／末尾空行，未编辑退出或 :wq 返回原正文，changed=False，不生成修订；比较初始 TextBuffer 的呈现值判断是否变动，未变动时返回原输入字符串，避免现有 splitlines 的规范化误判。实际编辑保留当前 buffer 行为，不实现新 Markdown 编辑器。通过替代编辑器与直接调用按键处理单元验证 :wq／:q／:q!／:h、禁止嵌套 edit 和委派 CLI 命令，不跑真实终端流程。
- [ ] **Step 5：确认绿并提交。** Run `python -m unittest tests.test_cli_writes tests.test_editor_contract -v`；失败后再次查看／编辑仍得到用户结果，无打开连接遗留；Commit message：`refactor: decouple terminal editor and preserve failed saves`。

### Task 12：退出旧运行接口、更新文档并完成验收

**Files:** Remove `src/file/sql.py`、`document.py`、`folder.py`；Modify `src/file/__init__.py`、`bootstrap.py`、`README.md`、`docs/文件格式.md`、`文件系统.md`、`架构分层.md`、`用户界面.md`；Create `tests/test_architecture.py`；Modify `tests/test_content_concurrency.py`。

**Interfaces:** `python -m src.file.bootstrap` 若保留，只转发管理 init 并打印弃用说明；明确旧数据必须 migrate-legacy，不能调用旧 initialize_data。src.file 不再导出 Document／Folder／SqlFile。所有新入口和服务 API 已由前述任务定义。

- [ ] **Step 1：写失败验证。** AST 检查 CLI／editor 不 import src.file 或 Repository；services 不 import 入口，core 不 import 存储，repository 不 import service／入口且没有事务控制调用。禁止 CLI paths／commands 用 Path／os 扫描内容目录；editor 读取自身 help.md 和 app 定位数据库合法，不能用粗略全文禁用 Path。`test_lock_timeout_is_storage_busy` 确认生产默认 5000ms，独立锁连接测试可注入短 timeout；`test_save_after_move_or_delete` 覆盖范围内移动可保存、范围外移动／删除拒绝。

  ```python
  def test_save_after_delete_fails_without_new_revision(self):
      doc = self.service.read_document(self.scope, self.document_id)
      self.other_service.delete_node(self.scope, doc.id, expected_version=doc.version)
      with self.assertRaises(NotFound):
          self.service.save_document(self.scope, doc.id, '旧 buffer',
                                     expected_revision_id=doc.revision_id)
  ```

- [ ] **Step 2：确认红。** Run `python -m unittest tests.test_architecture tests.test_content_concurrency -v`；首先应因旧 import／接口残留而失败，若已无残留则记录检查直接通过，不故意制造失败。
- [ ] **Step 3：移除旧实现并同步文档。** 将旧协议说明放进“迁移来源”，统一正式运行库路径、虚拟 main 根与作用域、管理命令、Repository／服务职责、冲突 buffer 处理和尚未实现能力。bin 只是兼容顶层目录，不把软删除描述为自动搬入 bin。README 的迁移进行中说明改为实际交付状态，链接本 spec／计划；保留旧数据样本，不提交生成库。
- [ ] **Step 4：跑完整验收。** Run `python -m unittest discover -s tests -v` 和 `git diff --check`；全部测试通过、无 diff 格式错误。确认 `python -m src.storage --help`、`python run_cli.py --help`、`python -m src.cli --help`、保留的 bootstrap `--help` 不产生运行数据库。不要启动交互 CLI／浏览器，也不在仓库 data 中执行 init／迁移。
- [ ] **Step 5：检查覆盖并提交。** 核对下表和 git status，只有授权源码／文档／测试，源样本散列未变化，运行库及临时产物未跟踪。Commit message：`docs: complete unified content kernel migration and validation`。

## Spec 覆盖核对

| Spec 要求 | 任务与主要证据 |
| --- | --- |
| 表、协议、外键、部分索引、不可变身份／修订 | 2、3；直接 SQL 约束和跨范围测试 |
| 初始化幂等、默认树、保护对象 | 4、6；现有数据不改、同名非顶层目录可操作 |
| 活动 scope、逐段路径、稳定 ID、树顺序 | 1、4、6；读事务、错误中间节点、移动后路径 |
| 创建、metadata、正文修订、no-op | 5；版本／时间／修订数及 JSON 类型 |
| 服务／DAO／Database 分工与原子性 | 2、3、5、6、7、12；注入失败和 AST 边界 |
| 跨进程同名／正文冲突、锁等待 | 5、12；Barrier＋独立连接、StorageBusy |
| 子树快照、递归软删除及历史保留 | 7；第二连接改深层数据、旧 token 被拒绝 |
| 旧协议核对、时间、指纹、原文／metadata | 8、9；8 个对象逐条核对、源散列不变 |
| 原子发布、不覆盖、重复导入、WAL 恢复 | 9；进程中断、竞争目标、既有编辑不变 |
| CLI 会话、补全、编辑入口、buffer | 10、11；多 CLI、替代编辑器、最初 revision |
| 旧接口退出、命令／文档一致、完整验收 | 12；全文测试、依赖检查、help 不写库 |

## 执行交接

本计划仅规划实施，尚未执行以上代码任务。开始实施前由用户审阅计划并选择执行方式：主 agent 顺序实施，或按任务委派并独立审查。若采用子 agent，每次只分配一个上述可独立验证的任务，并携带文件与接口约定；共享 content.py、repository.py 或 CLI 文件的任务不得同时修改。每项交接给出实际变更、测试命令和结果，主 agent 检查产物后再继续下一项。

建议主 agent 顺序实施：任务较多，但共享服务、Repository 和 CLI 接口依赖紧密；迁移与并发测试已经提供逐步验证，最后再安排独立整体审查。若选子 agent 模式，严格沿依赖链交接，不把整个内核重写作为一个大任务委派。
