# 单 SQLite 虚拟文件系统与共用内容内核设计

日期：2026-09-30。状态：待审阅；本文描述拟实施方案，当前产品代码仍使用旧存储协议。

## 1. 目标与本阶段范围

项目用于朋友之间共同创作架空小说。日常写作采用实时共享草稿，阶段成果采用作品／工作区级提交、分支和审核合并。本阶段建立这些能力依赖的内容与存储基础。

用户已确定的方向：

- 将每个文档／目录各自使用 SQLite 的协议改为一个统一 SQLite 数据库。
- 目录是虚拟结构，文档使用稳定 ID；媒体实际文件与数据库分开存储。
- 抽出通用内核操作，CLI 和后续 Web 入口调用同一套业务逻辑。
- 基本操作需要事务、结构约束和保存版本检查，不能依靠 CLI 或进程内锁保证并发安全。

本阶段实现：统一数据库、虚拟路径与目录、文档读写及修订、元数据、创建／重命名／移动／软删除、旧数据导入、现有 CLI 与编辑器接入，以及核心行为验证。

范围假设：先提供一个默认工作区和一个默认 `main` 内容分支。工作区、分支的作用域进入数据模型和 API，分支创建与切换、阶段提交图、审核合并、实时协作、用户权限和媒体上传分别在后续阶段实现。这里的内容分支与 Git 仓库的 `renko-dev` 开发分支无关。

## 2. 当前问题与方案选择

`src/cli/commands.py` 中的目录浏览、新建、对象登记、正文保存和修改时间更新属于共用内容操作。`src/cli/paths.py` 的路径解析与补全依赖宿主机 `Path`；终端编辑器依赖可直接读写数据库的 `Document`。因此只迁移 SQLite 文件不足以消除入口耦合。

当前一次创建涉及父目录数据库和新对象数据库，一次保存的正文与修改时间也分别提交。初始化时重新生成子目录列表会丢掉文档登记。新设计用一个事务完成一次操作，目录关系只保存一份，并让读取对象不再隐式创建表或修改数据。

| 方案 | 取舍 |
| --- | --- |
| 把旧对象表合并，继续由 Document／Folder 对象直接修改存储 | 初始改动少，但入口仍需协调跨对象事务，未来分支需要拆分对象身份与目录状态 |
| 分开保存对象身份、分支内目录状态和不可变正文修订，通过内容服务执行操作 | 推荐；明确事务边界，保留稳定 ID，允许不同分支以后共享对象与修订而独立修改目录和正文 |

## 3. 存储布局与数据结构

```text
data/c156.sqlite       正式运行的统一内容数据库
data/media/            后续媒体实际文件的存储位置
```

旧 `.folder` 和文档容器在导入期间保持原样。切换后运行时从数据库查询虚拟目录，不扫描旧目录同步对象，也不根据虚拟路径创建宿主机目录。

数据库协议版本使用 `PRAGMA user_version`；第一版为 1。打开数据库只检查协议版本，建表和升级是显式管理操作。连接配置集中管理，不在每次业务操作中重复执行建表脚本。

### 3.1 表及字段

ID 使用 UUID 字符串；旧对象 ID 原样保留。时间使用带时区的 UTC ISO 8601 字符串；旧时间值保留原始值，并在能够解析时生成对应的规范时间。

| 表 | 字段与用途 |
| --- | --- |
| `workspaces` | `id, name, created_at`；作品／工作区范围 |
| `objects` | `id, workspace_id, kind, created_at`；对象稳定身份，kind 保留 folder／document／executable／resource |
| `branches` | `id, workspace_id, name, root_object_id, created_at`；默认 main 分支及其根对象 |
| `entries` | `workspace_id, branch_id, object_id, parent_id, name, position, version, current_revision_id, metadata_json, created_at, modified_at, deleted_at`；对象在某个分支中的当前目录和内容状态 |
| `document_revisions` | `id, workspace_id, object_id, parent_revision_id, content, created_at`；不可变 Markdown 正文，parent_revision_id 表示此次保存基于的正文修订 |
| `legacy_imports` | `source_digest, imported_at, object_count, document_count, report_json`；完成导入的来源指纹和核对结果 |

`entries` 主键为 `(workspace_id, branch_id, object_id)`，其父对象引用同一工作区、同一分支的 entry。对象、分支和修订使用复合外键限制工作区及对象范围；相关复合列必须有匹配的主键或唯一约束，不能仅依赖单列 UUID 推断关系。

workspaces、objects、branches、document_revisions 的 id 各自为主键；branches 在 `(workspace_id, name)` 上唯一，legacy_imports 以 source_digest 为主键。document_revisions.parent_revision_id 同样只能引用本工作区、本对象的既有修订。

`current_revision_id` 只能指向同一工作区、同一对象的正文修订；文件夹为 NULL。首次创建文档也产生正文修订，即使正文为空。修订只追加，不通过公开 API 更新或删除，软删除文档也保留修订；数据库触发器禁止修订 UPDATE／DELETE。对象 ID、工作区归属与 kind 创建后不变。

`metadata_json` 保存扩展元数据，由 Python 严格序列化／解析，不要求 SQLite JSON 扩展。旧 metadata 表的键值完整导入：modified_at 提取到 entry 的规范时间字段，其他键保存到 metadata_json，原始键值整体留在导入报告中。正文、目录状态、版本计数等固定字段不通过元数据接口修改；get_metadata 返回扩展键值，规范修改时间通过 NodeSnapshot.modified_at 读取。

### 3.2 目录约束

- 每个分支只有一个未删除的根 entry：parent_id 为 NULL，name 为空。branches.root_object_id 指向它。根对象及默认 main／admin／resource／bin 顶层目录作为保护对象，禁止重命名、移动或删除。
- 非根名称非空，禁止 `/`、反斜杠、NUL、`.`、`..`；中文和空格允许。名称区分大小写，使用 SQLite BINARY 比较，不对旧名称自动改写。
- 未删除兄弟的名称和 position 分别唯一，使用部分唯一索引；根 entry 的唯一性使用独立的部分唯一索引，避免 NULL 绕过约束。
- parent_id 指向文件夹；活动对象的祖先均为活动文件夹。类型检查、祖先检查、根对象匹配在服务事务内执行，外键保证引用范围和对象存在。
- parent_id 是目录关系的唯一来源，不再维护单独的子对象 ID 表，也不持久化 fullpath。
- 移动前检查祖先链，禁止把目录移到自己或后代下面。修改位置不改变对象 ID 或正文修订。
- version 从 1 开始；目录、元数据、正文指针和删除状态改变时递增。直接子列表改变时，父文件夹也递增版本并更新修改时间。
- position 为非负整数，version 为正整数，在服务校验之外使用数据库 CHECK 约束。
- 新对象追加到父目录最后；移动到新父目录时追加，重命名保留顺序。列表 API 按 position 返回稳定顺序，CLI 可另行排序展示。

### 3.3 默认树与访问根

导入旧 `/data` 根为工作区根，其虚拟路径为 `/`；旧 main／admin／resource／bin 成为 `/main`、`/admin`、`/resource`、`/bin`，对象 ID 和父子关系保留。这几个目录首先承担兼容现有组织的作用，用户与全局配置后续使用专门数据模型。

CLI 的访问根仍为 main 对象，所以 CLI 中 `/products/concretecream` 与此前含义一致。API 使用不可变 `ContentScope(workspace_id, branch_id, root_id)` 明确访问范围；访问根限制目录遍历范围，用户权限后续在服务中检查，不能把调用者传入的 root_id 当作授权。

路径解析纯粹处理虚拟 POSIX 路径，支持绝对路径、相对路径、`.`、`..`、`~`、`~/`。cwd 以对象 ID 显式传入；向访问根外走报 PathOutsideRoot。路径解析及按 ID 操作均检查对象属于 scope 的活动子树，不能通过直接传 ID 绕过访问根。

对象的显示路径按当前父子关系计算。移动父目录后，后代显示路径自然变化；当前目录保存为 ID，重命名后仍能找到。若当前目录已经删除，CLI 提示并返回访问根。

## 4. 共用服务与内核 API

```text
CLI／后续 Web 适配器 → ContentService → SQLite 存储与事务
                            ↓
                     纯数据模型与虚拟路径规则
```

建议模块划分：

| 模块 | 责任与依赖 |
| --- | --- |
| `src/core/models.py` | 不可变的 ContentScope、NodeSnapshot、DocumentSnapshot、TreeItem 等结果类型；不打开数据库 |
| `src/core/errors.py` | 结构化领域错误；不输出终端文字或 HTTP 响应 |
| `src/core/paths.py` | 虚拟路径词法与名称规则；不访问宿主机文件系统 |
| `src/storage/database.py`、`schema.py` | 连接生命周期、事务、配置、显式初始化和协议版本检查 |
| `src/storage/repository.py` | 参数化 SQL；接收服务传入的连接，业务方法不自行提交；依赖 core 的纯数据类型 |
| `src/storage/legacy.py` | 旧容器的只读检查与离线导入，返回结构化报告 |
| `src/services/content.py` | 共用 ContentService：范围检查、目录规则、版本比较、事务协调，依赖 core 和 storage |
| `src/cli/*` | 命令解析、提示、显示、补全呈现和当前会话状态；调用服务 |
| `src/editor/*` | 终端输入和内存文本编辑；接收纯数据，不依赖旧存储对象 |

依赖方向为 storage → core，services → core／storage，入口 → services／core。core 不导入 CLI、编辑器或存储；services 不导入入口。首版使用具体 SQLite 存储实现，不引入通用插件或多数据库框架。

### 4.1 操作契约

以下是拟公开的结构化操作；调用者传对象 ID 和 scope，不传 SQL、宿主机容器路径或 CLI 命令字符串。

```python
resolve_path(scope, path, *, cwd_id=None) -> NodeSnapshot
get_path(scope, object_id) -> str
get_node(scope, object_id) -> NodeSnapshot
list_children(scope, folder_id) -> list[NodeSnapshot]
list_tree(scope, folder_id, *, max_depth=None) -> list[TreeItem]

create_folder(scope, parent_id, name) -> NodeSnapshot
create_document(scope, parent_id, name, *, content="") -> DocumentSnapshot
read_document(scope, object_id) -> DocumentSnapshot
save_document(scope, object_id, content, *, expected_revision_id) -> DocumentSnapshot

rename_node(scope, object_id, name, *, expected_version) -> NodeSnapshot
move_node(scope, object_id, parent_id, *, expected_version, name=None) -> NodeSnapshot
delete_node(scope, object_id, *, expected_version, recursive=False) -> None
get_metadata(scope, object_id) -> dict
set_metadata(scope, object_id, changes, *, expected_version) -> NodeSnapshot
```

NodeSnapshot 包含 id、kind、name、parent_id、position、version、scope 内显示路径、时间与元数据；DocumentSnapshot 再包含 content 和 revision_id。返回值是一次读取的结果，没有 content setter、sql 属性或隐式持久化行为；扩展元数据返回独立副本，调用者修改副本不改变存储。

`resolve_path` 默认 cwd 为 scope.root_id。文件类型检查由操作执行：读取正文只接受 document，列目录只接受 folder。list_tree 的 max_depth 必须为非负整数或 None，0 只返回起点；树读取处于一个读事务内，返回数据由 CLI 排版。

重命名、移动、删除和元数据修改需要调用者读取时的 entry.version；写入前比较，过期报 Conflict。移动与重命名不改正文基础修订，文档保存以 expected_revision_id 检测正文冲突，并在写事务内检查对象仍在活动访问范围内。

删除采用软删除；非空目录默认报 DirectoryNotEmpty，recursive=True 则在一次事务内软删除整棵子树，更新受影响 entry 版本和父目录版本。稳定身份、正文修订保留。恢复删除和资源回收策略在后续阶段添加，本阶段不清理历史正文。

`set_metadata` 只接收扩展键值的更新并保留未指定键；禁止修改 id、kind、parent_id、name、position、version、revision_id、created_at、modified_at、deleted_at 等结构字段。metadata 的 JSON 可序列化性在写事务前检查。

### 4.2 保存与事务

ContentService 每次调用使用独立连接；服务实例不保存 cwd、编辑器、用户 buffer 或共享可变数据库连接。组合操作在同一连接、同一事务中完成，Repository 不另开连接或 commit。

写操作使用短 `BEGIN IMMEDIATE` 事务：在取得写事务后检查当前父目录、目标名称、版本与祖先关系，再修改数据。数据库部分唯一索引和外键作为结构保障。只读操作使用读事务获取一致结果；初始化以外的读取不执行建表、补记录或创建目录。

正文保存流程示意：

```text
开始写事务
  验证 scope、活动对象和文档类型
  比较 current_revision_id 与 expected_revision_id
  不一致：Conflict，回滚，保持现有正文与历史
  正文完全相同：返回当前快照，不新增修订或更新时间
  插入新正文修订，其 parent_revision_id 为当前修订
  条件更新 entry 的正文指针、version 与 modified_at
提交事务并返回新快照
```

即使新正文相同，也先验证调用者的基础修订。更新正文、修订记录和修改时间必须一起成功或一起失败；条件更新未影响一行也作为冲突处理。以后实时协作增量通过专门协调组件进入服务，不能直接使用全文替换绕过在线协作状态。

连接创建时启用外键并配置 5 秒 busy timeout；显式初始化正式数据库时配置 WAL，打开连接时检查预期配置。采用 sqlite3 的显式事务方式，例如 isolation_level=None 配合 BEGIN／COMMIT／ROLLBACK，避免依赖 Python 版本不同的隐式事务默认行为。

锁等待超时转为 StorageBusy。领域冲突不自动重试，存储方法不在已经部分执行的操作中重试单条 SQL。终端编辑、确认提示、网络等待和媒体上传均在写事务之外发生。

### 4.3 错误与入口边界

统一错误包括 NotFound、AlreadyExists、NotDirectory、NotDocument、InvalidName、PathOutsideRoot、Conflict、DirectoryNotEmpty、ProtectedNode、InvalidMove、StorageBusy、UnsupportedSchema 和 MigrationError。CLI 转成可读文字，未来 Web 转成结构化响应；已知约束违反映射到领域错误，未知数据库故障保留原异常信息供开发诊断。

CLI 的 help／exit、参数解析、当前目录、创建确认、编辑器启动、树形排版和补全格式属于入口。路径查找、列目录、类型验证、创建登记、正文保存、目录完整性及未来权限检查属于服务。

ls、tree、cat、mkdir、edit、cd、pwd 和路径补全统一通过服务访问虚拟对象。cat 的对象类型由服务决定；宿主机普通文件不能通过目录扫描混入虚拟文件系统。

本阶段保留现有 CLI 命令集合。重命名、移动、软删除等新增核心操作先通过 Python API 和测试使用，后续入口可以各自增加交互方式。

编辑器改为接收纯编辑输入（文档 ID、显示标题、正文），返回编辑结果。CLI 保留最初读取的 revision_id，通过 save_document 保存。保存失败时保留结果并重新提供编辑或查看正文的机会，不能关闭编辑器后静默丢弃 buffer，也不能把旧 buffer 重新绑定到最新 revision_id 强制保存。交互期间没有数据库连接或事务一直打开。

本阶段 CLI 直接调用本地服务。后续常驻服务承载 ContentService 与协作会话时，在线 CLI 可通过结构化 API 接入；共用业务规则不依赖这种调用方式的变化。

## 5. 初始化、迁移与切换

新初始化与旧数据导入分别提供 Python 管理 API 及薄 argparse 入口，例如：

```text
python -m src.storage init --database data/c156.sqlite
python -m src.storage migrate-legacy --source data --database data/c156.sqlite
python run_cli.py --database data/c156.sqlite
```

init 只用于创建空的新数据库，创建一个默认工作区、main 内容分支及兼容的虚拟顶层目录。已初始化数据库再次 init 只验证协议与默认树，不重建子列表、不修改现有对象。存在旧数据时使用 migrate-legacy；CLI 启动不自动导入、修复或覆盖数据库，缺少数据库时给出明确的初始化／迁移命令。

### 5.1 导入前检查

迁移在停止旧协议写入的离线窗口执行。扫描真实目录收集所有 `.folder` 和文档容器，通过 SQLite URI mode=ro 读取，不调用会写入或建表的旧 Document／Folder 构造函数。

检查 ID 唯一、对象类型、路径与物理位置一致、父目录 ID 一致、名称合法、metadata 可解析，以及容器中只存在可识别的旧协议表。文件夹缺少有效 `.folder`、未知普通文件、未知内容类型／表、损坏数据库或矛盾引用都报错，不能静默跳过。

旧目录子对象列表与实际容器比较：

- 实际对象的位置与 parent_id 一致、但未登记时，允许根据实际容器补入，并在报告列出修复项；这是现有初始化逻辑可能造成的已知缺失。
- 已登记对象保留原 position 顺序；未登记对象按名称稳定排序，追加在最后，再分配连续的新 position。
- 悬空登记、重复 ID、一个对象登记到错误父目录，或物理位置与 parent_id 矛盾时，中止并报告。

源指纹由相对容器路径及其内容散列组成。扫描排除明确指定的目标数据库及其临时文件，不将其他未知文件自动忽略。源文件存在 WAL／journal 时要求先完成旧进程关闭和恢复，再重新迁移，避免遗漏未合入主数据库的修改。

### 5.2 原子导入

1. 只读检查全部源容器，收集报告和来源指纹。
2. 在目标目录中创建独占临时数据库，导入期间使用 rollback journal 模式；事务内插入工作区、对象、分支、entries、初始正文修订和导入记录。
3. 保留全部旧对象 ID，核对正文原文及其散列、元数据、父子关系与顺序。将旧 fullpath 和原始时间记录在报告中；正文不得重新格式化或改变换行。
4. 执行 integrity_check、foreign_key_check 及目录可达性检查；确认源指纹在检查结束时仍然一致。
5. 提交并关闭临时数据库，确保数据已经落盘，再使用同文件系统的原子、不覆盖发布方式建立目标文件，例如独占 hard link 后移除临时名称。已有目标不得被替换。
6. 正式数据库使用前由配置步骤启用 WAL。原始旧目录与数据库保持原样，导入报告通过管理 API 返回，并持久化到 legacy_imports。

失败时回滚并仅清理本次创建的临时产物。已有目标如果记录了相同来源的完成导入，重复迁移返回已有导入报告，不重新写入正文或覆盖迁移后的编辑；来源不同或没有匹配记录则报错。第一次失败不留下可被 CLI 当作有效运行库的半成品。

当前仓库可核对的基线为 7 个文件夹、1 篇文档。文档 `main/products/concretecream` 的 ID 为 `8b168849-1ab3-4542-9828-f2e2120f2d57`，迁移后仍通过 CLI 路径 `/products/concretecream` 访问。

### 5.3 仓库与旧接口

运行数据库及 WAL／SHM／journal、临时迁移文件加入 .gitignore。现有旧数据保留为迁移样本；开发与测试通过显式初始化或迁移生成运行数据库，避免将正在变化的内容库提交到代码 Git 仓库。

旧 `src/file` 的单容器实现由新模块替代，所有运行时调用点一并迁移；旧数据的读取逻辑集中在 legacy 导入模块。旧 bootstrap 入口如需保留，只作为新管理 API 的弃用转发入口，不再执行原来的目录重扫与登记覆盖。

README、文件格式、文件系统、架构分层和用户界面文档同步说明新协议、命令、入口边界及尚未实现的协作／版本能力。

## 6. 验收证据

验证使用标准库 unittest 和临时数据库，核心操作可在不启动 CLI、终端编辑器或浏览器的情况下调用。迁移测试使用复制的旧数据，不修改仓库中的样本。

- 初始化可重复调用；再次初始化后，已创建文档、ID、正文、顺序不变。
- 创建目录与文档、列目录、读写中文正文、元数据、移动与重命名均通过服务完成；移动后 ID 和后代路径正确。
- 注入操作中途失败时，正文、修订、元数据及目录关系一起回滚，不留半成品。
- 同名并发创建只有一个成功，另一个返回 AlreadyExists；独立进程／连接用于验证，不能只在一个进程内加锁模拟。
- 两个调用者基于同一正文修订保存时，后保存者返回 Conflict，先保存正文与历史保持正确。
- 正文未变化不生成新修订；旧正文修订不可通过公开 API 修改。
- 跨工作区、跨分支、访问根外的路径与直接 ID 请求均不能越界；缺失对象与错误类型返回对应错误。
- 禁止根目录修改、目录环、非法名称；非空目录默认不能删除，递归删除原子完成并保留正文修订。
- 不同 CLI 实例具有独立 cwd；改变目录、路径补全、树形排版与中文错误展示继续可用。
- CLI 编辑保存调用服务，带上最初的正文 revision_id；发生冲突时保留编辑结果。可使用替代编辑器验证调用路径，无需自动操作真实终端或浏览器。
- 旧样本导入后核对 8 个对象的 ID、正文、父子关系、metadata、目录顺序和 CLI 访问路径；源容器散列不变。
- 缺失登记可以有报告地补入；矛盾登记、损坏容器和未知文件导致迁移中止。中途中断、已有目标、重复导入均不覆盖现有数据。
- 运行时 CLI 与 editor 不再导入旧 SqlFile、Folder／Document 容器，也不使用 Path 扫描或修改虚拟内容目录；新的服务模块不导入 CLI 或 editor。

验收命令为 `python -m unittest discover -s tests -v`，配合 `git diff --check` 和依赖检查。测试聚焦迁移完整性、事务及并发行为，不增加浏览器交互测试。

## 7. 后续衔接

后续作品级提交可引用 entries 的目录状态和 document_revisions 的正文修订；分支拥有独立 entries，复用 objects 的稳定身份。新增提交、快照和合并请求表即可表达版本关系，本阶段的正文 parent_revision_id 不承担作品级 Git 提交图的职责。

实时协作库接入后，协作组件以工作区、分支和文档维护共享草稿，阶段快照从服务器已确认的一致状态生成。全文保存接口需要与活跃协作会话协调，不能直接覆盖 CRDT 状态。用户权限在共用服务边界实现，CLI 和 Web 无法通过不同入口获得不同业务规则。

媒体上传后，以资源对象与不可变资源版本引用独立文件；引用的访问控制、历史保留和文件回收由专门流程实现，文件系统副作用不宣称与 SQLite 事务天然原子。

## 8. 设计依据

- [已有共享草稿与阶段版本约定](../../架构分层.md#共享草稿与阶段版本)
- [SQLite 事务](https://sqlite.org/lang_transaction.html)：显式事务、写入协调与锁等待。
- [SQLite 外键](https://sqlite.org/foreignkeys.html)：每连接启用外键及复合引用的约束要求。
- [SQLite 部分索引](https://sqlite.org/partialindex.html)：活动兄弟与根节点的唯一约束。
- [Python sqlite3](https://docs.python.org/3/library/sqlite3.html)：显式控制连接与事务行为。
