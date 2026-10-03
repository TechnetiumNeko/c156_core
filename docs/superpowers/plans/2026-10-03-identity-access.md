# 用户与权限系统执行计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 执行方式由用户选择，不因本段自动启动委派。

**Goal:** 交付账号生命周期、工作区授权、私密创建与冻结，并让现有 Web、CLI 和所有公开内容操作执行同一套身份与权限规则。

**Architecture:** 沿用 Python 标准库入口、原生 HTML/JS 和单 SQLite 文件。公开应用用例持有事务，Identity、Access、Content 的 Repository 和事务内操作共享连接，内容与管理审计原子提交。Principal 只能从会话解析；ContentScope 只界定范围，授权始终检查真实分支根到目标的完整活动祖先链。

**Tech Stack:** 当前 Python 3.13.2、SQLite/WAL、unittest；原生 JS 模块、Node 22 检查；新增已获准的 `argon2-cffi==25.1.0`，不引入 Web/前端框架或其他认证算法。版本及参数 API 已核对 [PyPI](https://pypi.org/project/argon2-cffi/25.1.0/) 与 [官方 API](https://argon2-cffi.readthedocs.io/en/stable/api.html)。

**Spec:** [2026-10-02-identity-access-design.md](../specs/2026-10-02-identity-access-design.md)，以当前工作区中的已修改版本为准。

状态：全部13项已完成并验证；使用 subagent-driven development，实现 gpt-6.1-sol low、审查 gpt-6.1-sol medium；已通过最终全分支审查。调查基线为 `renko-dev` 的 `7075d59`；spec 有用户已有未提交修改，必须保留，不擅自覆盖或纳入其他提交。

## Global Constraints

- “本阶段面向新库开发，不建设既有数据库升级流程。” 定义 `SCHEMA_VERSION = 2`，拒绝版本 1，提示在新路径初始化；不删除、覆盖或自动升级运行库。
- “第一版使用当前默认工作区，不顺带实现多工作区创建与切换。” 保留 workspace/branch 复合约束。
- “各应用用例强制执行授权，授权模块集中计算策略。” 不保留缺身份即全权的公开接口、HTTP system 身份或演示后门。
- “Repository 与事务内服务不 commit、不重新连接”；读取与写入均在同一快照解析身份、成员与授权；写用例使用 `BEGIN IMMEDIATE`。
- 登录名 `3–32` 位 ASCII、以字母开头、字符集字母/数字/下划线/短横线、转小写唯一；显示名 `1–80` 字符。密码 `15–128` Unicode 字符，不截断、不去空格、不规范化。
- Argon2id：`memory_cost=19456` KiB、`time_cost=2`、`parallelism=1`、`salt_len=16`、`hash_len=32`；摘要计算/验证在写事务外，缺依赖明确报错。
- 会话与 activate/reset 凭据均由 `32` 字节安全随机数生成，存 SHA-256 摘要；会话 `24` 小时、activate `48` 小时、reset `1` 小时，不滑动续期。原始凭据只在生成时返回一次，不写日志。
- 限流窗口 `15` 分钟：登录名、凭据摘要、改密用户各最多 `10` 次；HTTP 直连来源地址最多 `60` 次认证请求，CLI 使用固定 `local` 来源桶。独立短事务先提交计数，每次最多清理 `100` 个过期桶，单表最多 `10000` 个活动桶，成功不清零。
- 最后一个 active 站点管理员不能停用、撤销管理员身份或发起重置；有效工作区 owner 按 `active/reset_required` 计算，不能停用、移除或降级至零。普通成员接口不能创建 owner。
- 成员、ACL、阅读范围、已有对象私密状态与冻结统一比较 `workspace_access_settings.version`，无变化也先比较；真实变更只递增一次。private 创建无需传授权版本，但成功递增一次。账号管理另比较 `users.version`。
- 跨目录移动仅工作区 admin/owner；recursive delete 对实际活动子树每个对象要求 read/delete，并检查冻结、保护与并发，不要求 edit/review/publish。
- 私密范围只能由有效成员中的私密所有者或本工作区 admin/owner 通过；不能跳过不可读祖先，不因停用/移除所有者而公开；普通编辑者仅在创建时选择 private。
- 冻结仅文档；管理角色也不能静默绕过他人冻结；祖先改名、移动、递归删除检查活动后代冻结。
- Cookie：`c156_session`、`HttpOnly`、`SameSite=Strict`、`Path=/`、无 Domain，期限不超过 `24` 小时。仅当前 loopback HTTP 允许省略 Secure；继续绑定 `127.0.0.1`，Host/Origin 端口必须匹配实际监听端口。
- UI 沿用当前组织和样式；草稿绑定用户，身份变化使旧响应失效；会话失效只暂停编辑。同用户重登不替换基础修订，换用户先处理旧草稿。
- 不实现邮件、开放注册、MFA、第三方登录、协作算法、作品分支/发布/恢复、数据库升级、公网部署或完整文件管理 UI；review/publish 只建模配置。
- 运行已有相关检查；永久测试只补关键新增行为缺口，不重复各层完整场景、不以源码字符串或 mock 断言代替行为。浏览器最多打开截图，不自动点击/填表。

## Review Focus

1. 密码验证期间账号被重置/停用：旧凭据校验成功也不能签发有效会话；计数不得随失败回滚。由任务 3 的真实 SQLite 竞争检查覆盖。
2. 缩小访问根或已知隐藏 ID：不能跳过祖先/私密规则，列表序号、访问根 parent_id、错误 details 不泄漏结构。由任务 7 覆盖。
3. 隐藏私密后代或冻结后代：删除预览不能泄漏，整个删除/祖先结构修改原子失败；只有 read/delete 仍能合法删除。由任务 9 覆盖。
4. 同时更改管理状态：最后管理员/owner 约束与授权版本必须在写事务中生效，失败审计/成员/内容全部回滚。由任务 4、6、8 覆盖。
5. 退出/换账号与在途加载/保存交错：旧响应不能复活内容、跨账号不能接管草稿，同账号保留原修订。由任务 11 的纯状态检查覆盖，不使用浏览器交互测试。

## 文件与接口落点

| 文件 | 所有权与职责 |
| --- | --- |
| `requirements.txt` | 唯一新增运行依赖及版本；不引入新的包管理方案 |
| `src/identity/{__init__,models,validation,passwords,tokens}.py` | 纯身份数据、账号输入规则、Argon2 适配器、随机凭据及摘要 |
| `src/access/{__init__,models,policy}.py` | 动作/角色/ACL/私密/冻结纯数据与集中策略；不打开数据库 |
| `src/storage/{identity_repository,access_repository,audit_repository,auth_throttle_repository}.py` | 参数化 SQL、记录映射；不持有事务、不决定权限 |
| `src/storage/schema.py`、`management.py`、`legacy.py` | v2 schema、新库/旧容器导入的初始授权数据；不迁移旧运行库 |
| `src/services/{unit_of_work,content_operations}.py` | 应用事务工作单元；从现有内容服务提取复用当前 Repository 的操作 |
| `src/services/{identity,accounts,access}.py` | 认证/本人账号、站点账号管理、工作区成员/授权/冻结公开用例 |
| `src/services/views.py` | Web 所需的纯数据组合结果，不持有连接或 HTTP 字段 |
| `src/services/content.py` | 保留 ContentService 名称，改为显式凭据、事务与内容授权的公开门面 |
| `src/identity/__main__.py`、`src/services/bootstrap.py` | 显式本机首管理员/owner 引导；HTTP/CLI 日常运行不调用 |
| `src/web/{http,api,app,serialization}.py`、新增 `auth.py` | Cookie/CSRF/来源与路由适配、公开字段序列化 |
| `src/web/static/{client,editor-state,app,directory}.js`，新增 `account.js`、`access.js` | 请求、用户草稿隔离、账号/成员/权限管理展示；不复制服务端授权算法 |
| `src/web/static/{index.html,styles.css}` | 当前页面内增加账号及管理面板、新建可见性、冻结操作 |
| `src/cli/{app,commands,paths,completion}.py` | 进程内会话、getpass、经过服务授权的命令/补全与失权回退 |
| `run_demo.py`、README 和现有开发文档 | 显式新演示库引导、正常登录、旧库拒绝说明及使用文档 |

上述新增文件同时建立包 `__init__.py`；只导出公开数据/适配器，事务内内容能力不从 `src.services` 对外导出。不改 vendor，不为本次任务拆分旧导入大模块。

### 公共类型和调用约定

下面是计划内的接口细化，执行者读 spec 和本文件后保持一致；发现需更换边界或另选依赖，先报告给主 agent，由主 agent提交用户选择。

```python
# identity/models.py；冻结 dataclass，公共结果不包含内部凭据字段
Principal(user_id: str | None, site_admin: bool)  # 仅事务内解析生成
UserView(id: str, login_name: str, display_name: str, status: str,
         site_admin: bool, version: int)
SessionGrant(user: UserView, session_token: str, csrf_token: str, expires_at: str)
AccountTokenGrant(user: UserView, token: str, purpose: str, expires_at: str)
SessionView(user: UserView, csrf_token: str, expires_at: str)

# access/models.py；相应集合不可变
AccessRule(object_id: str, subject_type: str, subject_key: str,
           action: str, effect: str)
MembershipView(user: UserView, role: str, status: str)
WorkspaceAccessView(version: int, read_scope: str,
                    members: tuple[MembershipView, ...])
ObjectAccessView(version: int, rules: tuple[AccessRule, ...], visibility: str,
                 private_owner_id: str | None, locked_by: str | None,
                 inherited_rules: tuple[AccessRule, ...],
                 decisions: Mapping[str, Decision])
ContentAccessView(version: int, actions: tuple[str, ...], visibility: str,
                  frozen: bool, can_freeze: bool, can_unfreeze: bool)
Decision(allowed: bool, reason: str, source_object_id: str | None)
# PolicyNode 为 access/models.py 的结构化 Protocol，只声明 object_id/kind；
# 存储 EntryRecord 满足该协议，纯策略不 import storage。

# services/views.py；只组合既有公开 DTO，不携带 Repository
NodeAccessView(node: NodeSnapshot, access: ContentAccessView)
DocumentAccessView(document: DocumentSnapshot, access: ContentAccessView)
BootstrapView(initialized: bool, session: SessionView | None,
              workspace_role: str | None, workspace_access_version: int | None,
              root: NodeAccessView | None)
```

- `session_token: str | None` 为公开用例**必传关键字参数**。`None` 明确表示游客，只允许 spec 的公共 read；缺参报错。非空但失效令牌抛 `Unauthenticated`，绝不降级游客。不接受调用方提供 Principal/user_id/role/system。
- 所有时间为 UTC ISO 字符串，服务注入 `clock: Callable[[], datetime]`（默认真实 UTC 时间），测试可推进时钟；不靠真实睡眠验证过期。
- 在 `src/core/errors.py` 增加 `Unauthenticated(code='unauthenticated')`、`Forbidden(code='forbidden')`、`RateLimited(code='rate_limited', details={'retry_after': 秒数})`、`Frozen(code='frozen')`，复用既有 Conflict/NotFound/InvalidArgument。公开不可见对象统一 NotFound 且空 details；删除遇隐藏后代统一 Forbidden 且空 details。
- `ApplicationUnitOfWork(database).transaction(*, write: bool=False)` 返回仅在 `with` 内有效的工作单元，提供同连接的 identity/access/audit/throttles Repository；`content(scope) -> ContentOperations`，`resolve_principal(token) -> Principal`。这里不产生 SQL，委托各 Repository；退出关闭并提交/回滚。
- `ContentOperations(repository: Repository)` 保留现有内容方法名、scope、版本/修订/token 参数和返回类型，去掉事务包装。新增 `default_scope()`、`get_entry(scope, object_id)`、`ancestor_chain(object_id)`、`active_subtree(object_id)` 作为内部组合能力；祖先链顺序固定为真实分支根到目标。不得从入口导入。
- `ContentService(database)` 保留现有 get_node/get_path/resolve_path/list_children/list_tree/get_metadata/read_document/create_folder/create_document/save_document/set_metadata/rename_node/move_node/prepare_delete/delete_node 的参数及结果；每个方法增加必传 `session_token`。create 方法再增加 `visibility: str='inherit'`；新增 `describe_access(scope, object_id, *, session_token) -> ContentAccessView`。`default_scope(*, session_token)` 同样验证根可读；启动内部固定 scope 使用工作单元内部方法，不预读匿名内容。
- 内容快照保持现有类型，不把完整 ACL、创建者、内部 session 或摘要塞进 metadata；普通可执行动作通过 `describe_access` 获取。ContentService 新增 `get_node_with_access(scope,object_id,*,session_token) -> NodeAccessView`、`read_document_with_access(...) -> DocumentAccessView`、`list_children_with_access(scope,folder_id,*,session_token) -> tuple[NodeAccessView,...]`，分别共享对应读方法的事务内实现。`bootstrap(*,session_token) -> BootstrapView` 在同一读工作单元中取得初始化状态、会话及固定默认 scope 的可见根/动作；匿名只返回 initialized，失效 token 报错，有效用户根不可达时 root=None。HTTP 只调用这些公开组合用例，不打开事务或直接访问 Repository；独立动作查询只用于提示，写入始终重新判断。

### 存储契约

`schema.py` 保留当前内容表与不可变约束，追加以下 v2 定义。`TEXT` 时间采用同一 UTC 格式；版本用 `INTEGER CHECK(typeof(version)='integer' AND version>=1)`；bool 用 `INTEGER CHECK(... IN (0,1))`。所有 FK 启用，默认 RESTRICT，不级联物理删除身份或审计。

| 表 | 主键、字段与必须存在的约束 |
| --- | --- |
| users | `id TEXT PK`，`login_name TEXT UNIQUE NOT NULL`，`display_name TEXT NOT NULL`，`status CHECK IN(invited,active,reset_required,disabled)`，`site_admin`，`version`、`credential_version`，`created_at/modified_at`；id/login_name 不可变，规范登录名验证使用 identity.validation |
| password_credentials | `user_id TEXT PK FK users`，`password_hash TEXT NOT NULL`，`credential_version INTEGER`、`updated_at` |
| account_tokens | `token_digest TEXT PK`，`user_id FK users`，`purpose CHECK IN(activate,reset)`、`credential_version`、`issued_at/expires_at/consumed_at/revoked_at`；用户/用途查询索引 |
| sessions | `token_digest TEXT PK`，`user_id FK users`、`credential_version`、`csrf_token TEXT NOT NULL`、`issued_at/expires_at/revoked_at`；user_id 索引 |
| workspace_memberships | `PK(workspace_id,user_id)`、FK workspaces/users，`role CHECK IN(reader,editor,admin,owner)`，`status CHECK IN(active,removed)`、`version`、`created_at/modified_at` |
| workspace_access_settings | `workspace_id TEXT PK FK workspaces`，`read_scope CHECK IN(members,authenticated,everyone) DEFAULT members`，`version DEFAULT 1` |
| access_rules | `PK(workspace_id,branch_id,object_id,subject_type,subject_key,action)`，对象复合 FK→entries；subject_type CHECK IN(user,role,authenticated,everyone)，action 为 spec 八动作，effect CHECK IN(allow,deny)；通用主体 key 固定空字符串且只能 read；role key 限四角色。user 主体另存 `subject_user_id`，CHECK 与 subject_key 相等，复合 FK→memberships，非 user 时为空 |
| content_ownership | `PK(workspace_id,object_id)` 复合 FK→objects，`creator_id` nullable FK users；对象初始化/导入也插入空归属；创建归属不可修改 |
| content_privacy | `PK(workspace_id,branch_id,object_id)` 复合 FK→entries，`owner_id NOT NULL FK users`、`created_at`；记录存在即 private，删除记录即 inherit |
| content_locks | `PK(workspace_id,branch_id,object_id)` 复合 FK→entries，`locked_by NOT NULL FK users`、`created_at`；INSERT/UPDATE 触发器拒绝非 document 对象 |
| audit_events | `id TEXT PK`，`actor_id` nullable FK users（本机引导前为空），`workspace_id` nullable FK workspaces，`event_type/target_type/target_id`、`before_json/after_json`、`created_at`；目标可为摘要以外的内部稳定 ID，不存密码/原始凭据/正文；追加不可变 |
| auth_throttles | `PK(bucket_type,bucket_key)`、`window_started_at`、`attempts INTEGER CHECK>=1`；window_started_at 索引用于有界清理；bucket_type 为 login/token/source/password |

user ACL 的 active 成员资格、create 的 folder 类型、有效 owner/admin 计数属于事务内服务规则；SQLite 不能表达的跨行状态不靠 CHECK 假装保证。初始化/导入为工作区插入 settings 并为所有对象补空归属，users/memberships 初始为空，等待显式引导。

## 执行顺序与交接

执行依赖：`1 → 2 → 3 → (4、5) → 6 → 7 → 8 → 9 → (10、12)`，`10 → 11`，全部批准后进入 `13`。执行中为减少等待细化调度：任务 5 只写纯策略文件，与任务 4 账号管理并行，UoW 数据加载在任务 7 接线；任务 10 HTTP 与任务 12 CLI 文件所有权分离。共享 schema、helpers、content 门面保持单一所有者。依赖任务须完成实现、相关验证和审查后才消费其产物；中间提交不是可对外使用的半成品，所有入口切换完成才做整体验收。

若用户选择委派：每次 prompt 必须包含绝对 cwd、该任务文件所有权、spec/计划路径、Global Constraints、精确输入输出接口、上一任务实际交接与验证命令；说明不是独占仓库，不撤销他人修改；禁止读取密钥、推送/部署、再委派。新的技术/架构决策回报主 agent，不能自行选型。主 agent 维护共享夹具和衔接文件，不同时委派两个任务修改它们。DSH 若使用，遵循 `dsh-subagent` skill 的原生 MCP 生命周期，保留 agent_id 与回调，不用 shell 短轮询。

### Task 1: v2 schema 与独立 Repository

**Files:** 新建四个 `src/storage/*_repository.py`；修改 `schema.py`、`management.py`、`legacy.py`；测试扩展 `tests/test_database.py`、`test_initialization.py`、`test_legacy_import.py`，新增 `tests/test_identity_storage.py`。新增运行依赖文件归任务 3。

**Interfaces:** Repository 构造均接收已有 connection；AccessRepository 再接收 `workspace_id, branch_id`，Identity/Audit/Throttle 不接内容 scope。Identity 提供按登录名/ID/摘要取记录、带版本更新、撤销用户所有会话/凭据；Access 提供取成员/settings/rules/privacy/locks/ownership 及条件版本更新；Audit `append(event) -> None`；Throttle `consume(bucket_type, bucket_key, *, limit, now) -> ThrottleResult`，结果字段 `allowed: bool, retry_after: int`，Repository 不抛业务拒绝导致计数回滚。记录类型放各 repository 内，公开 DTO 由后续服务组装。

- [x] 建立上一节全部表/索引/约束与 `SCHEMA_VERSION=2`；同步初始化及旧容器导入的新库种子。Repository 使用参数化 SQL，不 commit/连接/开事务；身份记录不传到入口。
- [x] 在真实临时库验证：v1 运行拒绝且原文件未改；新库有 settings/空归属且无账号；跨 branch/workspace ACL/private FK 失败；非文档锁失败；同规则重复失败；移除成员不自动删除 ownership/privacy/lock。
- [x] 限流在同一写锁内完成有界清理、现有桶检查、容量判断与计数；固定 15 分钟窗口，不刷新窗口起点。超过限制和满容量返回可重试时间，拒绝新桶不挤掉有效桶；已有桶在容量满时仍可正常计数。服务对所有适用桶消费并收集结果，先提交该独立事务，再向调用者抛 RateLimited；不能在 with 内抛异常撤回来源计数。
- [x] Run: `python -m unittest tests.test_database tests.test_initialization tests.test_legacy_import tests.test_identity_storage -v`；期望全通过，导入仍保留旧源内容。
- [x] 审查实际 schema/约束与 Repository 事务边界后提交：`feat(storage): define identity and access schema v2`；只暂存本任务文件。

### Task 2: 提取事务内内容操作与应用工作单元

**Files:** 新建 `src/services/unit_of_work.py`、`content_operations.py`；修改 `content.py`、`__init__.py`；复用现有内容测试、`tests/test_architecture.py`。

**Interfaces:** 提供前文 ApplicationUnitOfWork/ContentOperations 契约；当前 ContentService 的算法、版本语义、SQL Repository 方法和返回快照不变。暂时保留现有门面用于验证提取；任务 7–9 完成后移除所有无身份公开调用，过渡门面不得用于发布。

- [x] 将现有内容算法移到 ContentOperations，使用注入 Repository，不开启嵌套事务。读写与 sibling 冲突/Busy/Schema 翻译交给公开应用工作单元边界；保持原异常 cause 和范围保护。
- [x] 工作单元在同一连接构造 Repository；数据返回前完成快照组装，退出后没有游标/事务对象逃逸。`ancestor_chain` 不被 ContentScope.root_id 截断，完整性检查仍覆盖 deleted/非文件夹/循环祖先。
- [x] 运行既有 content/repository 检查验证重构；在现有回滚用例追加一个组合失败：先内容写入再审计写入失败，两部分均无持久化。该测试断言真实数据库结果，不断言内部调用序列。
- [x] Run: `python -m unittest tests.test_content_reads tests.test_content_writes tests.test_content_moves tests.test_content_deletes tests.test_content_concurrency tests.test_repository tests.test_architecture -v`；期望原行为保持。
- [x] 提交：`refactor(services): compose content operations within application transactions`。

### Task 3: 认证、会话与限流

**Files:** 新建 `requirements.txt`、`src/identity/{__init__,models,validation,passwords,tokens}.py`、`src/services/identity.py`；修改 `core/errors.py`、`unit_of_work.py`；新增 `tests/test_identity.py`，仅在必要时补任务 1 Repository 方法。

**Interfaces:** `PasswordHasher.hash(password: str) -> str`、`verify(encoded: str, password: str) -> bool`；`IdentityService(database, *, clock=...)` 提供 `login(login_name, password, *, source) -> SessionGrant`、`current_session(*, session_token) -> SessionView`、`logout(*, session_token) -> None`、`activate(token, password, *, source) -> UserView`、`reset_password(token, password, *, source) -> UserView`、`change_password(old_password, new_password, *, session_token, source) -> None`、`change_display_name(display_name, *, session_token, expected_version) -> UserView`。source 由可信入口构造，HTTP 直连地址/CLI local。

- [x] 添加 `argon2-cffi==25.1.0`，封装显式 Type.ID 参数；只保留适配器依赖库，纯模型不依赖库。缺依赖启动报安装指引；不捕获所有验证错误冒充密码不匹配。
- [x] 实现用户名/显示名/密码校验、32 字节随机令牌及摘要、固定成本虚拟账号摘要。每个认证用例先在独立短事务提交全部适用限流桶，再处理拒绝结果（多桶拒绝取最大 retry_after）；昂贵摘要在写事务外；返回统一失败提示，不区分不存在/invited/disabled/密码错误。
- [x] 登录取凭据快照 → 关闭读事务 → verify → 短写事务重查 active 与 credential_version/摘要版本 → 插入 session；改密同样重查当前会话/凭据代次后更新摘要及代次并撤销所有旧会话。activate/reset 在消费时重查用途、期限、状态、代次，消费/改密/启用一并提交，不隐式登录。
- [x] 验证 `15/128` 字符可用、`14/129` 拒绝、空格/Unicode 原样；真实 Argon2 摘要能验原密码；会话 `24h` 到期，不滑动；activate `48h`/reset `1h` 边界及重复/并发消费仅一次成功。
- [x] 用两个 Database 句柄和可控验证屏障制造“verify 后重置/停用，再签发”竞争，断言没有有效旧会话；拒绝身份的读取不退游客。以注入时钟验证第 11 次/第 61 次限流、窗口恢复、失败计数保留、清理/容量边界，不等待真实时间。
- [x] Run: `python -m pip install -r requirements.txt`；`python -m unittest tests.test_identity tests.test_identity_storage -v`；期望全通过。审计/日志中不得出现原始凭据，HTTP 序列化后续单独验证。
- [x] 提交：`feat(identity): authenticate accounts with versioned sessions and bounded throttles`。

### Task 4: 站点账号管理与显式首管理员引导

**Files:** 新建 `src/services/accounts.py`、`bootstrap.py`、`src/identity/__main__.py`；补 `identity_repository.py`、`access_repository.py`、`audit_repository.py`；新增 `tests/test_accounts.py`、扩展 `tests/test_storage_commands.py`；共享 fixture 在 `tests/helpers.py` 精简扩展。

**Interfaces:** `AccountService(database, *, clock=...)`：`list_users(*,session_token) -> tuple[UserView,...]`；`create_user(login_name,display_name,*,session_token) -> AccountTokenGrant`；`resend_activation/reset_user/disable_user/enable_user/set_site_admin(user_id,*,session_token,expected_version,...)`；仅 set_site_admin 增加 `enabled:bool`。resend/reset/enable 返回 AccountTokenGrant，disable/set 返回 UserView。`bootstrap_admin(database,login_name,display_name,password) -> UserView` 只能本机命令显式调用，无 system Principal 参数。

- [x] 引导命令固定 `python -m src.identity bootstrap-admin --database PATH --login-name NAME --display-name NAME`，密码经 getpass 两次输入，不接受密码参数；同事务创建 active 站点管理员+默认 workspace owner+审计。引导只允许 users 为空且无 owner 的完整新库；重复/并发引导最多一次成功。
- [x] 实现 invited 创建、48h 重发、reset_required 重置、disabled 停用、启用后新 reset 凭据；遵守 credential_version 更新、撤销全部会话/凭据与用户 version 检查。所有管理操作均验证当前站点管理员，target 不能成为 actor。
- [x] 每次修改在写事务内检查最后 active 站点管理员与相关工作区有效 owner；一个账号可能同时影响两种保留约束。重置 owner 不移除成员，active/reset_required owner 计数；所有权有效性不把 invited/disabled 算进去。
- [x] 实际服务测试：最后管理员 reset/disable/revoke 拒绝但正常改密可行；有其他 admin 时操作可行；disabled 不能凭旧 activate/reset 恢复；两个写连接竞争不能移除最后管理者。注入审计失败后用户、令牌、成员完全回滚。
- [x] fixture 提供显式 owner/editor/reader 账号和 token；纯内容回归允许管理连接播种测试会话，身份测试走真实 login。不要为每个内容测试重复昂贵 Argon2 引导，不提供产品免认证路径。
- [x] Run: `python -m unittest tests.test_accounts tests.test_identity tests.test_storage_commands -v`；期望全通过，`--help` 不创建库。
- [x] 提交：`feat(accounts): manage account lifecycle and bootstrap the first owner`。

### Task 5: 集中权限策略

**Files:** 新建 `src/access/{__init__,models,policy}.py`；新增 `tests/test_access_policy.py`；数据加载衔接 `unit_of_work.py`。

**Interfaces:** `AccessPolicy(principal,membership,settings,*,rules,privacy,locks,ownership)` 消费同事务加载的不可变数据；`decide(chain: tuple[PolicyNode,...],action: str) -> Decision`、`can_read(chain) -> bool`、`require_action(chain,action) -> None`、`require_unfrozen(records: tuple[PolicyNode,...],*,operation: str) -> None`。chain 完整性由内容操作验证，PolicyNode 不依赖存储层。冻结冲突与普通 ACL 判断分开。

- [x] 固定动作 read/edit/create/rename/move/delete/review/publish；默认 reader=read，editor=read/edit/create/rename/move/delete（跨目录额外 admin gate），admin/owner=本工作区管理和内容动作，不自动给 editor review/publish。
- [x] 实现根→目标逐节点 private/read 路径检查；其他动作目标→祖先最近规则，本层 user→当前角色→authenticated→everyone，通用主体仅 read；无规则回退角色/default read_scope，写入还要求 active 成员。site_admin 不作为内容 bypass。
- [x] 私密先于普通 allow；私密所有者必须有效成员，仍受祖先阅读/动作限制；workspace admin/owner 绕普通 ACL/private，但不绕冻结/保护。冻结不能给原锁主增加任何权限。
- [x] 少量纯行为用例验证两条 spec 继承示例、近层规则优先于远层用户规则、同层主体优先级、非成员 user allow 不授予写入、游客与失效凭据的差别、site_admin 无工作区绕过权、private 祖先不可被孩子 allow 穿越。
- [x] Run: `python -m unittest tests.test_access_policy -v`；期望全通过。任务 7–9 的真实库用例验证策略接线，不重复全部策略组合。
- [x] 提交：`feat(access): evaluate inherited actions and private ancestry centrally`。

### Task 6: 工作区成员、规则及阅读范围管理

**Files:** 新建 `src/services/access.py`；补 Access/Identity/Audit Repository；新增 `tests/test_access_management.py`。

**Interfaces:** `AccessService(database,*,clock=...)`：`workspace_access(scope,*,session_token) -> WorkspaceAccessView`、`object_access(scope,object_id,*,session_token) -> ObjectAccessView`；`add_member(scope,login_name,role,*,session_token,expected_version)`、`set_member_role(scope,user_id,role,*,...)`、`remove_member(scope,user_id,*,...)`、`transfer_ownership(scope,target_user_id,*,...)`、`set_read_scope(scope,read_scope,*,...)` 均返回 WorkspaceAccessView；`put_rule(scope,rule,*,...)`、`remove_rule(scope,rule,*,...)` 返回 ObjectAccessView。所有省略的关键字均为 session_token 和 expected_version，不接受调用者 Principal。

- [x] 成员添加仅规范登录名精确查找 active 用户；不提供工作区管理员全站模糊查询。admin 只管理 reader/editor，owner 才能任免 admin；不通过 set_member_role/add_member 制造 owner。transfer 将调用 owner 降 admin、目标现有 active 成员升 owner，一次提交。
- [x] 所有管理写入先在同事务鉴权与比较 workspace settings.version，真实变更一次递增，no-op 不递增但陈旧版本仍冲突；membership 自身 version 作为记录信息，不作为第二套外部并发协议。
- [x] ACL 验证唯一键、对象/branch/workspace、八动作、create 文件夹、通用主体仅 read、user 为有效成员；remove_rule 表示恢复继承。remove_member 删除其 user ACL，保留私密/创建归属/锁与历史用户，重新加入不复活旧例外。
- [x] 管理结果与审计同事务；普通内容接口不返回 ObjectAccessView，只有 admin/owner 可读完整规则和策略来源。ObjectAccessView 分开给出本对象 rules、祖先 inherited_rules 和各动作 decisions（含来源对象/角色基线/管理绕过原因），不混淆缺规则继承与显式 deny。保留 owner 的写锁检查复用任务 4 规则，不维护重复算法。
- [x] 真实服务检查越权授予 admin/owner、陈旧/no-op version、移除后重加无旧 ACL、两 owner 并发操作保留至少一个、转交目标非 active 拒绝、站点 admin 无成员时不能管理工作区。断言实际成员/版本与审计结果。
- [x] Run: `python -m unittest tests.test_access_management tests.test_accounts tests.test_access_policy -v`；期望全通过。
- [x] 提交：`feat(access): manage workspace membership and versioned rules`。

### Task 7: 所有内容读取授权与信息裁剪

**Files:** 新建 `src/services/views.py`；修改 `content.py`、`content_operations.py`、`unit_of_work.py`、`tests/helpers.py`；扩展 `test_content_reads.py`，新增 `tests/test_content_access.py`。其他内容回归 fixture 的身份切换跟随本任务；写用例切换由任务 8/9 完成。

**Interfaces:** 前述 ContentService 全部读方法增加必传 session_token；`describe_access` 普通结果只含 version/actions/visibility/frozen/can_freeze/can_unfreeze，不返回他人归属/完整规则。按公共契约实现 bootstrap 和三个 with_access 组合用例，复用事务内读取，不调用会重新开事务的公开方法。

- [x] 每个读事务先解析令牌与当前用户状态，读取成员/settings/rules/private，再读取内容；所有 ID/path/tree/metadata/default_scope 入口统一读门禁。较深 scope 仍读取完整分支祖先，逐路径段检查可达性。
- [x] list_children 只保留可读孩子，展示 position 从可见序列 `0` 连续编号；list_tree 不深入不可读目录。get_node 的展示 position 同样由父目录可见兄弟序列计算，不能只修列表；访问根 parent_id=None。不向客户端提供未过滤总数。
- [x] 授权先于目标类型、冲突和涉及隐藏对象的 details；不可见 ID/path 返回一致 NotFound，不提供隐藏名称、用户、数量和范围外祖先。创建时撞到隐藏同名对象使用不带目标名称/ID 的统一失败，不泄漏其快照。
- [x] 真实库覆盖隐藏祖先+孩子 allow、深访问根绕过、私密文件夹+公开孩子、已知 ID、元数据读取、非成员 everyone read，以及失效令牌不降游客；比较公开位置与错误内容，已隐藏对象不可从补全所用列表查到。
- [x] 更新既有读 fixture 为显式身份，保留原所有可观察断言；不因新增授权放宽范围/版本检查。
- [x] Run: `python -m unittest tests.test_content_reads tests.test_content_access tests.test_access_policy -v`；期望全通过。
- [x] 提交：`feat(content): authorize every read and hide inaccessible structure`。

### Task 8: 创建、正文与 metadata 授权，私密范围原子落盘

**Files:** 修改 `content.py`、`content_operations.py`、`access.py`、`core/json_values.py`；扩展 `test_content_writes.py`、`test_content_concurrency.py`、`test_content_access.py`。

**Interfaces:** 内容 create_folder/create_document 增加 visibility='inherit' 与必传 session_token；save_document/set_metadata 保留正文 revision/entry version 协议。AccessService 增加 `set_visibility(scope,object_id,visibility,*,session_token,expected_version) -> ObjectAccessView`；private owner 不接受外部参数。

- [x] 创建要求有效成员与父 read/create；private 额外角色 editor/admin/owner，reader 有 create 例外也不能 private。服务器填 creator_id/private owner，对象/首修订/归属/private/审计/授权版本在同一事务；inherit 也写创建归属，不无故递增授权版本。
- [x] 编辑要求完整阅读路径+edit+有效成员+文档冻结检查，保持 revision/version/no-op 规则。普通 metadata 不承载授权；扩展现有保留字段校验，明确拒绝 `acl/access_rules/visibility/private_owner_id/creator_id/locked_by`，不允许靠 metadata 取消实际策略。
- [x] 已有对象 visibility 仅 admin/owner 修改、比较授权版本；private owner 取初始 creator，空 creator 取本次 admin，不改历史归属。恢复 inherit 只删本对象 private，不清除后代记录；用户离开保留 private，普通保存不能更改它。
- [x] 真实服务检查 private 创建角色/父门禁、reader create 例外、site_admin 非成员不可读、原子创建在 audit 插入失败时无孤儿对象/修订/private/version；编辑撤权先提交阻止后写；私密作者移除/停用不公开；管理员恢复父 inherit 不公开独立 private 孩子。
- [x] 更新既有写与并发 fixture 显式凭据，保留 no-op/冲突/失败不落盘断言。
- [x] Run: `python -m unittest tests.test_content_writes tests.test_content_concurrency tests.test_content_access tests.test_access_management -v`；期望全通过。
- [x] 提交：`feat(content): create private content atomically and authorize editing`。

### Task 9: 结构修改、完整删除资格与冻结

**Files:** 修改 `content.py`、`content_operations.py`、`access.py`；扩展 `test_content_moves.py`、`test_content_deletes.py`、`test_content_access.py`；关键冻结用例放新增 `tests/test_content_locks.py`。

**Interfaces:** rename_node/move_node/prepare_delete/delete_node 按前文增加必传 session_token，其余参数及返回不变。AccessService 增加 `freeze_document(scope,object_id,*,session_token,expected_version) -> ContentAccessView`、`unfreeze_document(...) -> ContentAccessView`。

- [x] 重命名检查 rename；同父 move 按 rename，不额外要求跨目录管理员能力。跨目录检查源 move、目标 read/create、有效 admin/owner；保留现有 cycle/保护/version 规则。对象 ACL/private 随 ID 保留，继承随新祖先生效。
- [x] freeze 要求文档、完整 read/edit、有效创建者或 admin/owner；不能替换他人的锁；unfreeze 要求成员/完整 read、锁主或 admin/owner。同锁主重复 freeze/无锁 unfreeze 先比授权版本再 no-op；有效修改递增授权版本一次并审计。
- [x] 任何修改文档（正文、metadata、rename/move/delete）检查冻结者仍具本次正常权限；修改文件夹 rename/move/recursive delete 检查完整活动后代文档锁。其他作者/管理员先显式解锁，不能静默覆盖。
- [x] prepare_delete 和 delete 使用同一全子树资格函数，实际遍历未过滤记录；每个对象 read/delete+冻结+保护，不附加 edit/create/review/publish。隐藏/不可删后代统一 Forbidden 空 details，预览不返回部分子树/token。删除写事务重新鉴权，再比较 entry version/subtree token，全失败回滚。
- [x] 真实用例：甲的 private 草稿使乙删除共享目录及预览失败，公共兄弟均未删；取消 edit/review/publish 而保留全部 read/delete 后删除成功；预览后撤权不能依旧 token 删除；冻结后代使祖先改名/移动/删除失败；admin 解锁后可操作；移除锁主不自动解锁；private 移动保留自身记录。
- [x] 最终移除任务 2 无身份过渡门面，全部公开内容方法必传 token；原保护对象、版本、token 与失败回滚测试保持。Repository/ContentOperations 仅显式本机管理内部使用，不从 HTTP/CLI 调用。
- [x] Run: `python -m unittest tests.test_content_moves tests.test_content_deletes tests.test_content_locks tests.test_content_access tests.test_content_concurrency tests.test_architecture -v`；期望全通过。
- [x] 提交：`feat(content): authorize structural changes and enforce document freezes`。

### Task 10: HTTP 认证、传输边界与管理路由

**Files:** 新建 `src/web/auth.py`；修改 `http.py`、`api.py`、`app.py`、`serialization.py`；扩展 `tests/test_web.py`，新增 `tests/test_web_auth.py`。具体路由见下一表。

**Interfaces:** `RequestIdentity(session_token: str|None, source: str)` 由 Handler 从 Cookie/直连地址构造；`API.dispatch(method,path,query,body=None,*,identity)` 不接客户端 actor。`APIResponse(status:int, body:dict, session_grant:SessionGrant|None=None, clear_cookie:bool=False, retry_after:int|None=None)` 返回纯响应描述，Handler 格式化 Cookie。启动 nonce 继续由服务实例随机生成，登录后 `X-C156-CSRF` 使用 session csrf 值。

| 方法/路径 | 请求与应用用例 |
| --- | --- |
| GET `/api/bootstrap` | 匿名只返回 `initialized, nonce`；有效会话返回 `user,csrf,expires_at,workspace_access_version,workspace_role,root|null,root_access|null`，root 不可读时为空；不暴露 root_id/树给匿名。显式可公共阅读场景由内容读接口授权提供 |
| POST `/api/auth/login` | `{login_name,password}` → login，Cookie 携带 session，JSON 仅 SessionView；nonce、source 限流 |
| POST `/api/auth/activate`、`/api/auth/reset` | `{token,password}` → activate/reset_password；nonce、source 限流，不签会话 |
| GET `/api/session`；POST `/api/auth/logout` | 当前 SessionView；有效会话/CSRF logout，撤销与清 Cookie |
| PUT `/api/account/password`、`/api/account/profile` | `{old_password,new_password}` / `{display_name,expected_version}`；改密后清 Cookie |
| GET/POST `/api/admin/users` | 站点管理员 list_users/create_user；POST `{login_name,display_name}`；原始 activate token 一次性结果 |
| POST `/api/admin/users/activation`、`/api/admin/users/reset`、`/api/admin/users/disable`、`/api/admin/users/enable` | `{user_id,expected_version}` → 对应账号管理；激活重发/重置/启用凭据一次性结果 |
| PUT `/api/admin/users/site-admin` | `{user_id,enabled,expected_version}` → set_site_admin |
| GET/POST `/api/workspace/members` | list / `{login_name,role,expected_version}` 精确添加 |
| PUT/DELETE `/api/workspace/members` | `{user_id,role,expected_version}` / `{user_id,expected_version}`；JSON DELETE 同样强制 CSRF/Content-Type/body 校验 |
| POST `/api/workspace/ownership`；PUT `/api/workspace/read-scope` | `{target_user_id,expected_version}` / `{read_scope,expected_version}` |
| GET `/api/access` | query `object_id`，admin/owner 完整 ObjectAccessView |
| PUT/DELETE `/api/access/rule` | `{object_id,subject_type,subject_key,action,effect,expected_version}`；DELETE 无 effect，删除恢复继承 |
| PUT `/api/access/visibility` | `{object_id,visibility,expected_version}`；无 private owner 参数 |
| POST/DELETE `/api/document/freeze` | `{object_id,expected_version}`，返回普通 ContentAccessView |
| 既有 `/api/children`、`/api/document`、`/api/folder` | 保留方法与内容参数；create 增加可选 visibility；公开快照旁附 `access`，节点 ID 与动作属于同一读取结果 |

- [x] fields 从“全部值必须 str”改为每路由显式类型校验，int 拒 bool；拒未知/重复字段、客户端 actor 和 URL 凭据。scope 服务端固定，不接受请求覆盖 root/workspace/role。
- [x] Host 与 Origin 校验实际监听端口，保留 JSON 2 MiB、严格重复字段解析/CSP/静态白名单/no-store、不开放 CORS；所有 POST/PUT/DELETE/PATCH 都检查 JSON 与来源。登录/activate/reset 用 X-C156-Nonce，其他写入用有效 session 和独立 X-C156-CSRF。Origin 存在时必须严格相等；无 Origin 的非浏览器请求仍须 Host+有效 nonce/CSRF。
- [x] Cookie 只传 opaque session、不回 JSON、不存 Domain；期限按剩余有效秒数，HttpOnly/Strict/Path。Secure 构造参数由可信运行模式决定，当前只允许 loopback HTTP；保留 future HTTPS Secure 支持且本次不开放部署模式。
- [x] 匿名 bootstrap 可用于尚未引导的新库，但运行不引导/修 schema。无可访问根的有效用户仍可管理本人或站点账号；不能让根读取失败阻止登录/账号恢复。失效 Cookie 在 bootstrap 返回 401，不返回匿名内容。
- [x] HTTP 映射 401/403/404/409/429，Frozen=403；429 返回 Retry-After；字段按公开 DTO 白名单，未知异常 500。去掉可能包含原始输入/摘要的异常日志，保留不含秘密的错误类别与请求路径（不记凭据 query）。
- [x] 真实 HTTP 集成验证 Cookie、会话/nonce/CSRF、实际端口不匹配、用户伪造、匿名/过期状态、关键管理路由派发和一次原子 private 创建；其余策略复用服务测试，不在 HTTP 复制整套继承树。客户端 JSON 与日志不含会话 token/摘要/内部版本；原始 activate/reset 仅生成时例外返回。
- [x] Run: `python -m unittest tests.test_web tests.test_web_auth tests.test_architecture -v`；期望全通过。
- [x] 提交：`feat(web): expose authenticated content and management endpoints`。

### Task 11: 工作台账号管理与用户草稿隔离

**Files:** 修改 `static/client.js`、`editor-state.js`、`app.js`、`directory.js`、`index.html`、`styles.css`；新增 `static/account.js`、`access.js` 并加入 HTTP STATIC；扩展 `tests/web/client.test.mjs`、`editor-state.test.mjs`。UI 仅补现有样式，无需选框架/重做设计。

**Interfaces:** Client 增加表中路由对应具名方法，区分 nonce/csrf，不存 session token。`EditorState.setIdentity(userId: string|null,{discard:boolean=false}={}) -> boolean`：换到不同非空用户且有旧草稿而未 discard 返回 false；失效/退出 null 暂停保存并保留草稿归属，任何身份转换增加 epoch。`open(snapshot)` 绑定当前用户；beginLoad/beginSave 的内部 ticket 记录 epoch/userId，网络正文保持既有 object_id/content/expected_revision_id 三字段。同用户重登重取权限，不自动 open 当前服务器修订。

- [x] 启动先解析最小 bootstrap，显示初始化指引或登录/激活/重置；有效身份显示用户、退出、改密/显示名。站点管理仅站点 admin，成员/ACL/read_scope/visibility 仅工作区 admin/owner，转交/任免 admin 仅 owner；服务拒绝仍显示对应错误。
- [x] 新建表单增加 inherit/private；按父 actions/create 与角色提示可用选择。文档展示冻结/解除与可执行动作；readonly/会话失效暂停编辑，保存失败保留所有 draft/base/comparisonDraft。
- [x] 账号管理支持创建/列表/停启/重置/站点 admin；生成的凭据仅当前展示供人工转交，不写 storage/log。成员精确添加、角色/移除/所有权转交；规则添加/删除继承、read_scope；visibility 切换提交前明确可见范围可能扩大。每次管理提交携带显示时的 access/user version，冲突后重新取配置，不静默重试覆盖。
- [x] identity epoch 同时保护目录加载、bootstrap、正文加载、保存、latest/merge 及账号/权限请求；退出期间返回的旧响应不能更新任何页面状态。失效时使旧 ticket 作废但不丢已写草稿；同账号重登保留原始 revision，刷新权限后再允许保存。
- [x] 不同账号已认证但进入工作台前，保留旧草稿并要求用户明确处理/放弃；新账号不得查看、自动继承或保存旧草稿。主动退出有未保存工作先沿用现有确认，拒绝退出时不撤销会话；确认后暂停并撤销，晚到保存无法复活旧内容。
- [x] 纯状态测试：同账号重登 draft/revision 不变；不同账号未 discard 被阻止，显式 discard 后清除；退出/失效 epoch 后旧 load/save/latest 不更新；保存中继续输入和 CRLF/合并保护既有用例继续通过。客户端检查 CSRF 头、typed JSON、session token 不在请求/响应缓存；只针对真实协议缺口添加用例。
- [x] Run: `node --experimental-default-type=module --test tests/web/client.test.mjs tests/web/editor-state.test.mjs`；期望全通过。页面最多打开桌面/窄屏截图，不自动点击/填表，截图不声称已验收全部管理交互。
- [x] 提交：`feat(workbench): add account controls and isolate drafts by user`。

### Task 12: CLI 身份、私密新建及失权回退

**Files:** 修改 `src/cli/{app,commands,paths,completion}.py`；扩展 `test_cli_reads.py`、`test_cli_writes.py`、`test_cli_completion.py`、`test_editor_contract.py`；不改 editor 的持久化边界。

**Interfaces:** CLI/VirtualFileSystem 接收显式进程内 session_token；CommandContext 和 PendingEdit 记录用户归属，所有 fs/service 调用携带 token；`mkdir [--private] PATH`、`edit [--private] PATH`（private 仅新建，已有对象拒绝该选项），新增 `login`/`logout`，密码 getpass。凭据激活/重置初期使用 Web，CLI 不另建完整管理命令树。

- [x] 启动 prompt 用户名并 getpass 密码，通过同一 IdentityService.login(source='local')；不把密码/令牌放 argv、文件或 shell 历史。`--help` 仍不访问库/认证；无账号提示显式本机引导，取消/EOF 可退出。
- [x] ls/tree/cat/cd/edit/metadata（既有调用）与 completion 只使用授权服务，所有调用添加 token，无 Repository 旁路。private 选项不接受 owner/ACL，错误保留草稿。
- [x] ensure_cwd 失权回到 scope 内可读祖先，根不可达标记“无可用内容”，不反复重试根。先解析 login/logout/exit 等会话命令，再执行内容 cwd 检查，避免没有根或令牌失效时无法重登/退出。
- [x] 会话失效暂停写入、保留 PendingEdit 的原 user/revision；同用户 login 重试仍用旧 base，不同用户须明确处理/放弃旧草稿，不能转用；退出仍遵守现有编辑器/草稿保护。编辑器与 prompt 在事务外运行。
- [x] 更新既有 CLI fixture 显式身份，验证私密孩子不出现在补全、无根能退出/重登、私密新建实际不可被另一 editor 读取，以及认证失败后的原正文/base 保留；不复制服务层完整 ACL 用例。
- [x] Run: `python -m unittest tests.test_cli_reads tests.test_cli_writes tests.test_cli_completion tests.test_editor_contract -v`；期望全通过。
- [x] 提交：`feat(cli): authenticate virtual filesystem sessions and private creation`。

### Task 13: 演示链路、文档及最终验证

**Files:** 修改 `run_demo.py`、README、`docs/工作台开发.md`、`代码导览.md`、`架构分层.md`、`文件格式.md`、`用户界面.md`、`services/__init__.py`；必要现有启动/初始化测试。更新本计划复选框和实际验证记录，不覆盖用户 spec。

**Interfaces:** prepare_demo 新库路径显式初始化 → getpass 首管理员/owner 引导 → 正常 login 后经授权 ContentService 播种示例 → logout；返回内部固定 scope 和非秘密启动信息。新用户凭据不硬编码/落日志；既有 v2 演示库只校验/打开，未引导时给命令指引，不启动自动抢注；v1 给新路径指引而非删除。

- [x] 调整 `is_running`，不能再依赖匿名 bootstrap 的 root.id；用最小非秘密服务标识并以服务端 nonce/固定实例标识确认是 C156，端口被其他服务占用仍保留既有换端口行为。标识不含数据库绝对路径/用户/内容结构。不因匿名访问失败当成需要重建演示库。
- [x] 文档记录安装 requirements、新路径 init/旧容器导入、bootstrap-admin、登录/激活/重置、角色/私密/冻结边界；删除“无需运行依赖/尚无认证”等过时说明。区分工作区 owner 与 private 创建者、授权 version 与正文 revision；记录不支持旧运行库升级及已授权业务范围。
- [x] 在临时新库验证 init → 显式 bootstrap → 正常 login → create private → logout；另查 v1 拒绝、旧库/源样本保持、启动 help 无副作用。使用已有 fixture/一次性脚本，不为演示、文案新增独立大型永久测试项目。
- [x] Run: `python -m unittest discover -s tests -v`；期望全部通过，不能靠删断言/放宽预期隐藏授权接线失败。
- [x] Run: `node --experimental-default-type=module --test tests/web/*.test.mjs`。实际净化用已有 jsdom 路径：`C156_JSDOM_PATH=/tmp/c156-web-checks/node_modules/jsdom node --experimental-default-type=module --test tests/web/*.test.mjs`；若该环境未安装，按既有文档在仓库外安装 `jsdom@26.1.0` 后执行一次。只报告实际结果/skip，不将 skip 当通过。
- [x] Run: `git diff --check`；检查样本/vendor 无变更、公开入口无未认证分支、未引入自动建库/升级/系统身份；独立审查整个实现的事务/策略/草稿及残留入口，由用户选择的执行流程安排。发现问题只修具体缺口并重跑相关检查。
- [x] 提交：`docs: document authenticated workbench and explicit setup`。交付说明包含实际命令与结果、截图范围、未执行浏览器交互和未交付的范围；无用户授权不推送、部署或合并。

## 计划自审与开工门槛

| Spec 范围 | 对应任务 |
| --- | --- |
| 3/4 模块边界、统一事务、可信身份 | 1、2、3、7–10、12 |
| 5 账号、密码、凭据、会话、限流 | 1、3、4、10–13 |
| 6 工作区角色、阅读基线、站点分离、所有权 | 4–7、10、11 |
| 7 继承、列表裁剪、结构、删除、冻结、私密 | 5、7–12 |
| 8 存储、审计、统一授权版本、新库协议 | 1、2、4、6、8、9、13 |
| 9 Web/CLI/演示、CSRF、草稿与错误契约 | 7、10–13 |
| 10 验收不变量与已有回归 | 各任务验证步骤及任务 13 |

- [x] 已读取当前 spec（包括未提交修改）、现有内容/事务/入口/前端状态与相关夹具，计划沿用已确认技术路线。
- [x] 每项新增行为有负责文件、输入/输出契约及检查位置，关键输入风险按最合适的一层覆盖；重构/样式/文案不机械新增永久测试。
- [x] 已核对公开数据与内部身份字段分离、version 命名/用途、root 可达性、删除不增加 edit 权限，以及跨账号 in-flight 结果隔离。
- [x] 用户已选择执行方式：本会话逐项实现；或 subagent-driven development，每项独立实现/审查后交接。建议后者用于本阶段：账号、策略、内容和两个入口边界清晰，权限错误需要独立审查；任务 2、7–9、11 由能处理跨模块约束的执行者负责。
- [x] 开工时按 using-git-worktrees 检测当前隔离环境；需要隔离时创建工作区，不在 dirty checkout 丢弃/自动暂存用户 spec。将当前 spec 原样带入隔离工作区作为设计输入，保持来源工作区修改。先跑既有 Python/前端基线一次并记录后再改代码。
- [x] 执行中出现超出 spec 的依赖/存储/接口/模块边界决策，提交方案、取舍和推荐理由供用户确认，等待期间只继续独立调查；不得先实施再追认。

执行完成：2026-10-03，全部13项任务已实施并独立审查；最终全分支审查的两项客户端响应问题已在 d90ecf0 集中修复并复核通过。Python完整检查458项通过；最终前端16项通过（含实际HTML净化，零跳过）。原始样本、vendor和用户已有spec修改保留。未执行浏览器/终端交互、截图、推送、部署或合并。

## 实际交付与验证

- 分支：`codex/identity-access`；隔离工作区：`/data/sunyunbo/www/c156_core/.worktrees/identity-access`。
- `python -m unittest discover -s tests -v`：458项通过，31.583秒；此后仅修复前端响应隔离及更新文档，Python源码未变。
- `C156_JSDOM_PATH=/tmp/c156-web-checks/node_modules/jsdom node --experimental-default-type=module --test tests/web/*.test.mjs`：最终16项通过、0失败、0跳过。
- `git diff --check`、模块语法、已有临时演示链路、15个原始样本/vendor文件与5个vendor清单散列、用户spec散列与93个本地文档链接检查通过。
- 关键证据：真实SQLite中的账号/凭据/限流竞争、管理保留与审计回滚；真实内容授权/私密祖先/完整删除/冻结；HTTP路由/Cookie/CSRF/端口/错误边界；CLI身份/无根/草稿；纯生产前端状态的保存/最新正文/合并代次。
- 未执行真实浏览器渲染、截图、点击/填表、交互流程或真实终端编辑器操作；没有将这些检查作为产品交互验收。没有开放协作/发布/旧运行库升级或部署。
- 初次整体验证后，最终审查发现保存确认前更新权限、丢弃后同文档最新正文响应过期两项问题；一次修复波次 d90ecf0 后限定复核均为 ADDRESSED，无未修复的 Critical/Important 问题。
