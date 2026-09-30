# 单 SQLite 虚拟文件系统与共用内容内核设计

日期：2026-09-30。状态：本阶段实现已接入统一 SQLite 与共用服务，最终集成审阅待完成；本文保留设计契约与后续范围。

## 1. 目标与本阶段范围

项目用于朋友之间共同创作架空小说。日常写作采用实时共享草稿，阶段成果采用作品／工作区级提交、分支和审核合并。本阶段建立这些能力依赖的内容与存储基础。

用户已确定的方向：

- 将每个文档／目录各自使用 SQLite 的协议改为一个统一 SQLite 数据库。
- 目录是虚拟结构，文档使用稳定 ID；媒体实际文件与数据库分开存储。
- 抽出通用内核操作，CLI 和后续 Web 入口调用同一套业务逻辑。
- ContentService 与 DAO 分层：服务统一业务规则与操作事务，DAO 统一 SQL 和数据映射。
- 基本操作需要事务、结构约束和保存版本检查，不能依靠 CLI 或进程内锁保证并发安全。

本阶段实现：统一数据库、虚拟路径与目录、文档读写及修订、元数据、创建／重命名／移动／软删除、旧数据导入、现有 CLI 与编辑器接入，以及核心行为验证。

范围假设：先提供一个默认工作区和一个默认 `main` 内容分支。工作区、分支的作用域进入数据模型和 API，分支创建与切换、阶段提交图、审核合并、实时协作、用户权限和媒体上传分别在后续阶段实现。这里的内容分支与 Git 仓库的 `renko-dev` 开发分支无关。

本阶段的“回滚”指失败操作的事务回滚，不包含用户主动恢复历史版本。正文修订为后续恢复提供数据基础，但历史浏览、正文恢复、软删除恢复及作品级回退接口均不在本阶段交付；目录和元数据当前也没有完整历史快照，不能宣称已经支持作品回滚。

## 2. 迁移前问题与方案选择

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

新建对象的 created_at／modified_at 使用本次操作的同一 UTC 时间；这些字段非 NULL，deleted_at 在活动状态下为 NULL。导入为整批数据记录同一个 imported_at：objects／entries.created_at 使用可解析且带时区的旧 created_at，否则使用 imported_at；entries.modified_at 使用可解析且带时区的旧 modified_at，否则回退到该 entry.created_at。初始正文修订的 created_at 使用 imported_at，表示修订入库时间。缺失、非法或无时区的旧时间均不得按本机时区猜测；报告逐对象记录原值、采用值及回退原因，区分历史时间与导入推定时间。旧 created_at 与 modified_at 都作为规范字段提取，不放入扩展 metadata；原始键值仍完整留在报告中。

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

`metadata_json` 保存扩展元数据，由 Python 严格序列化／解析，不要求 SQLite JSON 扩展。旧 metadata 表的键值完整导入：created_at／modified_at 按上述规则提取，其他键保存到 metadata_json，原始键值整体留在导入报告中。正文、目录状态、版本计数等固定字段不通过元数据接口修改；get_metadata 返回扩展键值，规范修改时间通过 NodeSnapshot.modified_at 读取。

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

直接子列表变化包括新增、删除、移入、移出和子节点重命名；子节点正文或元数据变化不向祖先传播版本。同一次操作对同一个受影响 entry 最多递增一次 version，并使用同一修改时间。递归删除的并发保护使用子树快照校验，不将目录 version 当作整棵子树的版本。

### 3.3 默认树与访问根

导入旧 `/data` 根为工作区根，其虚拟路径为 `/`；旧 main／admin／resource／bin 成为 `/main`、`/admin`、`/resource`、`/bin`，对象 ID 和父子关系保留。这几个目录首先承担兼容现有组织的作用，用户与全局配置后续使用专门数据模型。

CLI 的访问根仍为 main 对象，所以 CLI 中 `/products/concretecream` 与此前含义一致。API 使用不可变 `ContentScope(workspace_id, branch_id, root_id)` 明确访问范围；访问根限制目录遍历范围，用户权限后续在服务中检查，不能把调用者传入的 root_id 当作授权。

首版只有一个工作区；启动时选择该工作区的 main 分支，从 branches.root_object_id 对应根目录下按固定名称解析 main／admin／resource／bin，并验证它们是活动文件夹。保护规则只针对这些顶层对象 ID 及分支根，不影响其他位置的同名目录。CLI 使用解析出的 main ID；缺失或类型不符报 UnsupportedSchema，不自动补建。将来支持多工作区时必须显式选择工作区，不任取第一条记录。

路径解析纯粹处理虚拟 POSIX 路径，支持绝对路径、相对路径、`.`、`..`、`~`、`~/`。cwd 以对象 ID 显式传入；向访问根外走报 PathOutsideRoot。路径解析及按 ID 操作均检查对象属于 scope 的活动子树，不能通过直接传 ID 绕过访问根。

core.paths 只做分词、名称校验和前缀识别，不预先折叠 `..`；ContentService 在同一个读事务内逐段查询。继续处理下一段（包括 `.`／`..`）前，当前节点必须是活动文件夹：`document/../other` 报 NotDirectory，`missing/../other` 报 NotFound。尾随 `/` 要求最终节点为文件夹；连续 `/` 视为一个分隔符，空路径报 InvalidArgument。只有开头的 `~` 或 `~/` 表示访问根，`~name` 按普通名称处理；绝对路径和根前缀不依赖 cwd，相对路径要求 cwd 是范围内的活动文件夹。

对象的显示路径按当前父子关系计算。移动父目录后，后代显示路径自然变化；当前目录保存为 ID，重命名后仍能找到。若当前目录已经删除，CLI 提示并返回访问根。

## 4. 共用服务与内核 API

```text
CLI／后续 Web 适配器 → ContentService → DAO／Repository → SQLite
                            ↓
                     纯数据模型与虚拟路径规则

ContentService 决定事务范围，Database 组件管理连接与提交／回滚。
一次操作中的所有 DAO 方法使用同一连接与事务。
```

建议模块划分：

| 模块 | 责任与依赖 |
| --- | --- |
| `src/core/models.py` | 不可变的 ContentScope、NodeSnapshot、DocumentSnapshot、TreeItem、DeleteSnapshot 等结果类型；不打开数据库 |
| `src/core/errors.py` | 结构化领域错误；不输出终端文字或 HTTP 响应 |
| `src/core/paths.py` | 虚拟路径词法与名称规则；不访问宿主机文件系统 |
| `src/storage/database.py`、`schema.py` | 实现连接与事务上下文、配置、显式初始化和协议版本检查；事务范围由服务决定 |
| `src/storage/repository.py` | 承担 DAO 职责：参数化 SQL、批量查询、条件更新及数据库记录到纯数据类型的映射；接收服务传入的连接，不另开连接或提交 |
| `src/storage/legacy.py` | 旧容器的只读检查与离线导入，返回结构化报告 |
| `src/services/content.py` | 共用 ContentService：范围检查、目录规则、版本比较、事务协调，依赖 core 和 storage |
| `src/cli/*` | 命令解析、提示、显示、补全呈现和当前会话状态；调用服务 |
| `src/editor/*` | 终端输入和内存文本编辑；接收纯数据，不依赖旧存储对象 |

依赖方向为 storage → core，services → core／storage，入口 → services／core。core 不导入 CLI、编辑器或存储；services 不导入入口。首版使用具体 SQLite 存储实现，不引入通用插件或多数据库框架。

### 4.1 ContentService 与 DAO 的职责

代码统一使用 Repository 名称，由 `src/storage/repository.py` 承担 DAO 职责；两者在本文指同一数据访问层，不增加一层仅作转发的 DAO。

| 层 | 负责内容 | 示例 |
| --- | --- | --- |
| ContentService | 访问根与业务范围、对象类型、名称规则、保护对象、目录环、版本冲突、操作步骤与事务范围；后续统一执行用户权限检查 | 判断一次移动是否合法，协调源对象与两个父目录的修改 |
| DAO／Repository | 参数化 SQL、工作区／分支过滤、关联与祖先查询、排序、记录映射、条件写入及受影响行数 | 查询祖先链，执行带 expected_version 的 UPDATE |
| Database／Schema | 连接配置、事务机制、协议版本、外键、唯一约束和触发器 | 在服务指定的事务结束时 commit 或 rollback，阻止重复兄弟名称 |
| CLI／Web 适配器 | 输入解析、会话状态、展示与响应格式 | 显示 Conflict 的文字，或映射为 HTTP 冲突响应 |

服务中不散落 SQL，DAO 不处理交互、HTTP 响应或用户权限判断。DAO 返回 EntryRecord／RevisionRecord 等纯数据记录或受影响行数，由服务检查语义并组装 NodeSnapshot／DocumentSnapshot；显示路径在服务中生成。

DAO 使用传入连接及明确的 workspace_id／branch_id 建立数据访问范围；查询和更新必须包含对应范围，正文修订按工作区与对象过滤。访问根、活动祖先和未来用户权限仍由服务验证，不能把 DAO 的范围过滤等同于授权。祖先链、子树等批量查询可以使用 SQL JOIN 或递归查询，结果用于服务执行目录规则。

服务通过 Database 事务上下文取得连接，再把同一连接交给本次操作的 Repository。DAO 方法不自行 begin、commit、rollback、重连或执行 schema 初始化，也不调用 ContentService。入口的日常内容操作只调用服务，不直接调用 DAO。

例如移动目录的分工为：

```text
ContentService 进入写事务
  DAO 查询源 entry、目标目录和祖先链
  ContentService 检查访问范围、活动状态、类型、保护对象、版本与目录环
  DAO 条件更新源 entry 的父目录、名称、顺序和版本
  ContentService 根据受影响行数确认操作成功
  DAO 更新旧／新父目录的版本与修改时间
ContentService 结束事务，由 Database 提交；异常则整体回滚
```

依赖数据库当前状态的检查与修改位于同一个事务中，不能先在事务外检查，再开事务写入。新名称等不依赖数据库状态的输入校验可以在事务前完成。数据库约束保留为并发和结构保障，不因服务已有检查而省略。

### 4.2 操作契约

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
prepare_delete(scope, folder_id) -> DeleteSnapshot
delete_node(scope, object_id, *, expected_version, recursive=False,
            expected_subtree_token=None) -> None
get_metadata(scope, object_id) -> dict
set_metadata(scope, object_id, changes, *, expected_version) -> NodeSnapshot
```

NodeSnapshot 包含 id、kind、name、parent_id、position、version、scope 内显示路径、时间与元数据；DocumentSnapshot 再包含 content 和 revision_id。返回值是一次读取的结果，没有 content setter、sql 属性或隐式持久化行为；扩展元数据返回独立副本，调用者修改副本不改变存储。

`resolve_path` 默认 cwd 为 scope.root_id。文件类型检查由操作执行：读取正文只接受 document，列目录只接受 folder。list_tree 的 max_depth 必须为非负整数或 None，0 只返回起点；树读取处于一个读事务内，返回数据由 CLI 排版。

重命名、移动、删除和元数据修改需要调用者读取时的 entry.version；写入前比较，过期报 Conflict。移动与重命名不改正文基础修订，文档保存以 expected_revision_id 检测正文冲突，并在写事务内检查对象仍在活动访问范围内。

删除采用软删除；非空目录默认报 DirectoryNotEmpty，recursive=True 则在一次事务内软删除整棵子树，更新受影响 entry 版本和父目录版本。稳定身份、正文修订保留。恢复删除和资源回收策略在后续阶段添加，本阶段不清理历史正文。

递归删除采用“删除调用者已确认的子树状态”的语义。prepare_delete 在一个读事务内检查范围、文件夹类型和保护规则，返回不可变 DeleteSnapshot，包含目标 ID、version、活动子树的 TreeItem 列表（含起点）及 subtree_token。token 为作用域、目标 ID 和按 object_id 排序的全部活动子树 `(object_id, version)` 的规范 JSON（固定字段顺序、紧凑分隔符、UTF-8）之 SHA-256；不持久化新表。recursive=True 只接受文件夹且必须提供该 token，否则报 InvalidArgument。删除在 BEGIN IMMEDIATE 内重新读取子树并同时校验目标 version 和 token；期间任何节点新增、移出、删除、重命名、正文或元数据变化均报 Conflict，整次删除不生效。不得自动刷新 token 重试。非递归删除不接受 token；递归操作只修改仍活动的节点，保留此前已删除节点的时间与版本。

无变化操作仍先完成范围、类型、保护规则和预期版本检查。同名重命名、同父且同名移动、未改变扩展键值的 metadata 更新返回当前快照，不递增版本或更新时间。同父移动但改名等同重命名，保留 position，父目录版本只递增一次；本阶段不提供同父目录内重新排序操作。

`set_metadata` 只接收扩展键值的更新并保留未指定键；禁止修改 id、kind、parent_id、name、position、version、revision_id、created_at、modified_at、deleted_at 等结构字段。metadata 的 JSON 可序列化性在写事务前检查。

metadata 的顶层及嵌套对象键均必须是字符串；值只接受 JSON 的对象、数组、字符串、有限数值、布尔值和 null，拒绝 NaN、Infinity 及隐式类型转换。Python None 表示 JSON null，不表示删除键；本阶段不提供删键接口。非法 metadata 或操作参数报 InvalidArgument，保留字段也包括 workspace_id、branch_id、object_id、current_revision_id。相同键值按严格 JSON 类型和值比较，对象键顺序不影响相等，布尔值与数值不视为相同。旧扩展 metadata 同样按此验证，非法值导致 MigrationError 并保留诊断信息。

### 4.3 保存与事务

ContentService 每次调用通过 Database 管理独立连接；服务实例不保存 cwd、编辑器、用户 buffer 或共享可变数据库连接。组合操作在同一连接、同一事务中完成，DAO 不另开连接或 commit。

写操作使用短 `BEGIN IMMEDIATE` 事务：在取得写事务后检查当前父目录、目标名称、版本与祖先关系，再修改数据。数据库部分唯一索引和外键作为结构保障。只读操作使用读事务获取一致结果；初始化以外的读取不执行建表、补记录或创建目录。

正文保存流程示意：

```text
ContentService 进入写事务
  DAO 查询活动 entry 和当前正文修订
  ContentService 验证 scope、活动对象和文档类型
  ContentService 比较 current_revision_id 与 expected_revision_id
  不一致：Conflict，回滚，保持现有正文与历史
  正文完全相同：返回当前快照，不新增修订或更新时间
  DAO 插入新正文修订，其 parent_revision_id 为当前修订
  DAO 条件更新 entry 的正文指针、version 与 modified_at
  ContentService 检查受影响行数并组装新快照
结束事务，由 Database 提交，再返回新快照
```

即使新正文相同，也先验证调用者的基础修订。更新正文、修订记录和修改时间必须一起成功或一起失败；条件更新未影响一行也作为冲突处理。以后实时协作增量通过专门协调组件进入服务，不能直接使用全文替换绕过在线协作状态。

条件更新的 SQL 同时限制工作区、分支、对象、活动状态及预期 version／current_revision_id。DAO 返回受影响行数，ContentService 把过期写入解释为 Conflict，并触发整个事务回滚；DAO 不负责强制覆盖或业务重试。

连接创建时启用外键并配置 5 秒 busy timeout；显式初始化正式数据库时配置 WAL，打开连接时检查预期配置。采用 sqlite3 的显式事务方式，例如 isolation_level=None 配合 BEGIN／COMMIT／ROLLBACK，避免依赖 Python 版本不同的隐式事务默认行为。

锁等待超时转为 StorageBusy。领域冲突不自动重试，存储方法不在已经部分执行的操作中重试单条 SQL。终端编辑、确认提示、网络等待和媒体上传均在写事务之外发生。

### 4.4 错误与入口边界

统一错误包括 NotFound、AlreadyExists、NotDirectory、NotDocument、InvalidName、InvalidArgument、PathOutsideRoot、Conflict、DirectoryNotEmpty、ProtectedNode、InvalidMove、StorageBusy、UnsupportedSchema 和 MigrationError。CLI 转成可读文字，未来 Web 转成结构化响应；已知约束违反映射到领域错误，未知数据库故障保留原异常信息供开发诊断。

DAO 的查询缺失以空结果返回，条件写入以受影响行数返回；明确可识别的数据库约束故障可封装为存储错误，由服务结合操作语义映射。只有兄弟名称的唯一约束冲突映射为 AlreadyExists，不能将所有 IntegrityError 都当作同名错误。连接和锁故障由 Database 层封装为存储错误，再由服务提供统一的 StorageBusy 等结果。

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

init 只用于创建空的新数据库，创建一个默认工作区、main 内容分支及兼容的虚拟顶层目录。已初始化数据库再次 init 验证协议与默认树，并按 5.2 节幂等完成运行配置，不重建子列表、不修改现有对象。存在旧数据时使用 migrate-legacy；CLI 启动不自动导入、修复或覆盖数据库，缺少数据库时给出明确的初始化／迁移命令。

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
6. 迁移管理入口在已发布目标上启用 WAL，并验证协议、默认树及 journal_mode 后才成功返回。原始旧目录与数据库保持原样，导入报告通过管理 API 返回，并持久化到 legacy_imports。

发布前失败时回滚并仅清理本次创建的临时产物。发布后启用 WAL 失败或进程中断时，保留已完整导入的目标，不删除或覆盖它；管理入口报告配置未完成并提示重跑原迁移命令。legacy_imports 表示数据导入完成，不单独代表运行配置就绪。已有目标如果记录了相同来源的完成导入，重复迁移先验证协议及默认树，再幂等完成 WAL 配置和验证，最后返回已有导入报告，不重新写入正文或覆盖迁移后的编辑；来源不同或没有匹配记录则报错。CLI 遇到非 WAL 目标时明确拒绝启动并提示通过管理入口完成配置，不自行修改 journal_mode。目标不完整时不能作为有效运行库打开。

新 init 同样在完整建库后、成功返回前完成 WAL 配置；已初始化数据库再次 init 允许幂等完成运行配置，内容和默认树仍只验证、不修复。管理命令启用 WAL 失败必须保留可诊断错误，不能打印成功。

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
- 多个 DAO 方法由同一个服务操作调用时共享连接与事务；验证后续步骤失败会撤销先前 DAO 写入，禁止 DAO 提前提交。
- 验证 DAO 的工作区／分支过滤及条件更新受影响行数，服务将过期版本映射为 Conflict；访问根、目录环、保护对象等业务规则通过服务验证。
- 同名并发创建只有一个成功，另一个返回 AlreadyExists；独立进程／连接用于验证，不能只在一个进程内加锁模拟。
- 两个调用者基于同一正文修订保存时，后保存者返回 Conflict，先保存正文与历史保持正确。
- 正文未变化不生成新修订；旧正文修订不可通过公开 API 修改。
- 使用直接 SQL 验证修订 UPDATE／DELETE 触发器拒绝修改；无变化重命名、移动和 metadata 更新不改变版本、时间与顺序，但过期版本仍报 Conflict。
- metadata 拒绝非字符串键、非有限数值及保留字段，None 保存为 null；嵌套副本修改不影响存储。
- 独立连接持有写锁超过 busy timeout 时返回 StorageBusy，无部分写入。保存与移动交错时，在访问范围内移动不导致正文修订冲突；移出范围或删除后保存失败，旧 buffer 保留。
- 跨工作区、跨分支、访问根外的路径与直接 ID 请求均不能越界；缺失对象与错误类型返回对应错误。
- 路径逐段校验：文档／不存在节点后接 `..`、尾随 `/`、连续 `/`、空路径和越过访问根均符合上述契约；错误默认树不会被静默修复，其他位置同名目录不被误判为保护对象。
- 禁止根目录修改、目录环、非法名称；非空目录默认不能删除，递归删除原子完成并保留正文修订。
- 获取 DeleteSnapshot 后，另一连接修改深层正文／metadata、新增或移出后代，旧 token 删除均报 Conflict；无变化时删除成功。已软删除后代的删除时间和版本保持不变。
- 不同 CLI 实例具有独立 cwd；改变目录、路径补全、树形排版与中文错误展示继续可用。
- CLI 编辑保存调用服务，带上最初的正文 revision_id；发生冲突时保留编辑结果。可使用替代编辑器验证调用路径，无需自动操作真实终端或浏览器。
- 旧样本导入后核对 8 个对象的 ID、正文、父子关系、metadata、目录顺序和 CLI 访问路径；源容器散列不变。
- 缺失登记可以有报告地补入；矛盾登记、损坏容器和未知文件导致迁移中止。中途中断、已有目标、重复导入均不覆盖现有数据。
- 注入发布后、WAL 配置前中断及配置失败；CLI 明确拒绝未就绪目标，重跑迁移完成配置且 ID、正文、修订数和已有编辑不变。init 配置失败同样可重跑恢复。
- 验证当前样本缺失时间的回退，以及带时区、无时区、非法旧时间；同批回退时间一致，报告包含原值、采用值与原因。
- 运行时 CLI 与 editor 不再导入旧 SqlFile、Folder／Document 容器，也不使用 Path 扫描或修改虚拟内容目录；新的服务模块不导入 CLI 或 editor。
- 日常入口不直接导入或调用 DAO；DAO 不导入 services、CLI 或 editor，不含交互与响应格式逻辑；服务的业务流程不包含直接 SQL。

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
