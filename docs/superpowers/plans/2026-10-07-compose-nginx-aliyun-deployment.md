# Docker Compose 与自动部署 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for inline execution. Steps use checkbox (`- [ ]`) syntax for tracking. 用户已授权开始实施，不重复索取实施授权；新的重要架构变更仍须确认。

**Goal:** 交付可供朋友使用的两容器 Compose、ACR/GitHub Actions 流水线、一条部署入口、简短首次部署说明和排错清单。

**Architecture:** 面板现有 Nginx 管理站点及 TLS，转发到 loopback 高位端口；frontend Nginx 容器托管 Vue 并代理 backend。CI 构建测试精确提交的镜像，通过 SSH 传配置和同版本源码，主机数据保持 bind mount。

**Tech Stack:** 现有 Vue/TypeScript/FastAPI/SQLite；Docker/Compose、Nginx、Bash、GitHub Actions、已有 ACR 个人版。无新增服务或证书管理器。

**Spec:** [已批准设计及用户补充](../specs/2026-10-07-compose-nginx-aliyun-deployment-design.md)。

## Global Constraints

- 宿主机应用端口默认 127.0.0.1:28156，可配置；公网 Nginx 仍使用 80/443。
- frontend 内部8080、backend内部8001；backend 不发布宿主机端口。单实例、单worker。
- 复用已有 Docker 28、Compose 2.32.1、Nginx 1.26.3，不升级系统，不安装 Certbot，不覆盖面板 TLS 配置。
- 数据目录默认 /srv/c156/{data,assets,backups}，使用 bind mount。初始化和 bootstrap-admin 仍是显式操作。
- 保留 Host/Origin/CSRF、Secure Cookie 和2 MiB JSON契约；代理来源只信任项目网络172.30.156.0/24，可配置。
- CI 与手工使用同一部署脚本，精确 SHA/digest，不使用 latest 决定生产版本，不在服务器 git reset/pull。
- CI 同步 source.tar.gz 便于排查；不包含运行数据、.git、未跟踪凭据。同步不删除服务器持久化数据。
- 真正的 ACR 推送/朋友服务器部署需要配置和启动授权，本次先本地验证。不假称云端已部署。
- 前端样式不改；不做浏览器点击/填表。新增测试仅覆盖实际生产边界、备份和失败恢复风险。
- 原有未提交身份设计文档和其他项目保持原样；实现使用独立 worktree。

## Review Focus

1. 面板证书和其他站点不能被项目更新替换（Task4）。
2. Nginx和镜像切换失败后仍应保留数据与原成功发布信息（Task4）。
3. 多次构建乱序完成，旧任务不能覆盖新部署（Task4/5）。
4. 活跃WAL内容不能因复制单个数据库文件丢失（Task3）。
5. 容器内转发地址、公开Origin和Secure Cookie必须相容，不能所有用户共享代理来源桶（Task1/2）。

## 文件与责任

| 文件 | 责任 |
| --- | --- |
| src/server/config.py、__main__.py、app.py、routes.py | 显式生产配置、受信代理和无敏感数据readiness |
| tests/test_server_production.py | 新生产边界的真实服务检查 |
| deploy/backend.Dockerfile、frontend.Dockerfile、frontend-nginx.conf、compose.yaml、.dockerignore | 精确两镜像、容器代理与持久化编排 |
| deploy/smoke.py | 最终镜像的真实HTTP、版本和重建持久化验证 |
| src/storage/backup.py、__main__.py、tests/test_backup.py | 显式在线一致备份，不自动恢复 |
| deploy/config.env.example、setup.sh、common.sh | 单配置、环境检查和首次目录/权限准备 |
| deploy/nginx-proxy.inc.template、deploy.sh、rollback.sh | 项目代理片段、锁、精确发布、健康检查和失败恢复 |
| tests/test_deployment.py | 实际生产脚本的关键失败及文件保留行为 |
| .github/workflows/ci.yml、deploy/package.sh | 检查→构镜→smoke→ACR→SSH；版本包及源码归档 |
| deploy/README.md、TROUBLESHOOTING.md、README.md | 最少步骤交接、维护者配置及症状索引 |

### Task 1：生产配置与readiness

**Files:** 修改 src/server/{config,__main__,app,routes}.py；新增 tests/test_server_production.py。

**Interfaces:** ServerConfig 新增 mode='local'、public_origin=None、trusted_proxy_cidrs=()、build_sha='dev'；默认本地行为不变。production 要求 HTTPS origin、明确信任CIDR和Secure Cookie；仅该模式允许0.0.0.0。CLI增加 --production、--public-origin、--trusted-proxy-cidr，同时支持容器 C156_PUBLIC_ORIGIN/C156_PROXY_NETWORK/C156_DATABASE/BUILD_SHA。

- [ ] 新增简短用例：本地仍拒绝公开监听；production 缺失/非法origin或宽泛信任失败；有效HTTPS登录返回Secure Cookie，非法Host/Origin/CSRF仍失败。
- [ ] 生产运行启用 Uvicorn proxy_headers，forwarded_allow_ips 来自校验后的CIDR；本地保持False，不使用通配 *。检查真实 ProxyHeadersMiddleware 经可信/非可信peer产生的客户端身份。
- [ ] GET /api/healthz 返回 {status:'ok',build_sha:...}，短读事务验证数据库可用，不返回用户、对象ID或nonce。数据库不可用返回净化503。
- [ ] `python -m unittest tests.test_server_production tests.test_server_startup tests.test_server_transport tests.test_server_api tests.test_architecture -v` 通过。只覆盖新风险，不重写已有账号矩阵。
- [ ] 提交 `feat(server): add explicit container production settings`。

### Task 2：两镜像与Compose

**Files:** 创建 deploy/{backend.Dockerfile,frontend.Dockerfile,frontend-nginx.conf,compose.yaml,smoke.py}、.dockerignore。

**Interfaces:** 镜像 ARG BUILD_SHA 烧入 backend环境和 frontend /build-info.json。Compose消费 BACKEND_IMAGE、FRONTEND_IMAGE、SITE_DOMAIN、DEPLOY_ROOT；默认 APP_PORT=28156、PROXY_NETWORK=172.30.156.0/24、APP_UID/APP_GID=10001。backend内数据库/data/c156.sqlite，资产/assets，备份/backups。

- [ ] 从官方发行核对可用基础镜像及digest，锁定 Python3.13 bookworm slim、Node22和Nginx稳定版；多阶段前端构建，运行时无Node。后端使用非root，复制完整src及管理入口，依赖先于代码。
- [ ] Compose只绑定 frontend 127.0.0.1:${APP_PORT}:8080；backend无ports；显式environment，不泄露registry/SSH凭据。长语法bind mount拒绝静默创建错误宿主路径，日志轮转、健康检查、重启策略和停机宽限。
- [ ] frontend代理 /api 保留路径/Host/Origin及入口规范化转发头；2m上限；静态资源不存在返回404，index/build-info不长期缓存。
- [ ] 在/tmp建临时目录，显式运行镜像管理命令初始化及引导，凭据仅内存。smoke.py 通过实际frontend端口验证首页、静态构建SHA、API健康、登录、读写和退出；HTTP smoke手工携带Secure Cookie只证明API链路，真实浏览器TLS行为由HTTPS测试/上线检查另证。
- [ ] 重建两个容器后再读同一文档，证明数据保留。运行 docker compose config、最终镜像smoke；停止仅本次测试资源。
- [ ] 提交 `feat(deploy): containerize frontend and backend with bind mounts`。

### Task 3：备份与简短首次准备

**Files:** 新增 src/storage/backup.py、tests/test_backup.py、deploy/{config.env.example,common.sh,setup.sh}；修改 src/storage/__main__.py。

**Interfaces:** `backup_database(source: Path, target: Path) -> Path`；`python -m src.storage backup --database PATH --output PATH`。目标不能同源或覆盖已有文件，临时快照0600、校验后无覆盖发布。common.sh 的 `load_config(ROOT)`、`compose_for(RELEASE)`、`check_environment()`供Task4复用，不eval任意配置文本。

- [ ] 测试真实WAL写入后备份仍有新正文；已有目标保留、同源拒绝；拷贝快照到独立恢复目录，经既有init管理入口配置WAL后实际ContentService读取一致。
- [ ] 实现SQLite backup API与完整性/协议校验，不直接cp活跃库，不修改运行数据库，不加自动恢复。
- [ ] setup.sh只检查现有Docker/Compose/端口、建立项目目录、生成一份配置和目录权限；仅明确 --init-db 时执行现有init/bootstrap-admin交互。普通setup/deploy不隐式建库或重置账号。
- [ ] 配置必填只有 SITE_DOMAIN；DEPLOY_ROOT、APP_PORT、PROXY_NETWORK、APP_UID/GID提供默认/自动检查。NGINX_MANAGED=1表示项目include；可设0复用面板自身全站反代。不要求Certbot。
- [ ] 运行 `python -m unittest tests.test_backup tests.test_storage_commands -v`、`bash -n deploy/common.sh deploy/setup.sh`；配置检查用一次性实际脚本检查，不为默认值堆永久测试。
- [ ] 提交 `feat(storage): add verified backup and deployment preparation`。

### Task 4：共用部署、代理片段与回退

**Files:** 新增 deploy/{nginx-proxy.inc.template,deploy.sh,rollback.sh}、tests/test_deployment.py；必要时扩展common.sh。

**Interfaces:** `bash deploy/deploy.sh ROOT RELEASE_DIR`，release包含 compose.yaml、release.env（SHA、镜像digest、DEPLOY_SEQUENCE）及模板/脚本/source.tar.gz。`bash deploy/rollback.sh ROOT`恢复上一成功发布；NGINX_BIN默认command -v nginx，使用现有binary的 -t/-s reload，不假设systemctl服务名称。

- [ ] 独立proxy.inc仅包含location内部代理指令：loopback upstream、Host、覆盖XFF为$remote_addr、XFP为$scheme、2m上限；不包含TLS/server/location，不修改面板站点。首次文档指导在全站反代location include该文件。
- [ ] 严格验证SHA/digest、路径、端口和递增DEPLOY_SEQUENCE。使用flock协调项目部署；拒绝比成功发布更旧的任务。
- [ ] 拉取确切镜像→受限备份→启动候选→检查两镜像SHA/health→备份并原子更新proxy.inc→完整nginx -t→reload→HTTPS验证→原子记录current/previous成功版本。不删除data/assets/backups/.env。
- [ ] 失败时恢复原片段和原成功镜像；首次无上一版本仅报告失败并保留现场。恢复失败不能吞掉或标成功。NGINX_MANAGED=0跳过文件与reload，但仍做HTTPS验证。
- [ ] 添加短测试调用真实生产脚本：候选conf非法时原文件和发布记录保留；过期版本拒绝且data不变；首次失败不伪造回退。借助实际临时Nginx/最终容器执行校验，不写源码字符串断言或只断言mock调用顺序。健康回退用隔离两个版本镜像观测旧版本恢复。
- [ ] shell语法/静态检查、相关tests及真实隔离回退检查通过；不改开发机器已有Nginx站点或朋友服务器。
- [ ] 提交 `feat(deploy): add locked rollout and safe proxy rollback`。

### Task 5：GitHub Actions与发布包

**Files:** 新增 .github/workflows/ci.yml、deploy/package.sh。

**Interfaces:** CI工作目录检出github.sha；package.sh产生固定commit目录，含source.tar.gz、compose、模板、脚本、release.env。ACR_REGISTRY/ACR_NAMESPACE为Variables，ACR_USERNAME/ACR_PASSWORD为Secrets；production Environment含DEPLOY_HOST/PORT/USER/ROOT及DEPLOY_SSH_KEY/DEPLOY_KNOWN_HOSTS。

- [ ] PR/main运行既有Python、旧Node（真实jsdom）、新前端test/typecheck/build；设置timeout、contents:read。PR不使用发布凭据。
- [ ] main检查通过后load两镜像，运行Task2实际smoke，再推同一产物到ACR，单平台默认linux/amd64，provenance/sbom关闭；取得真实digest。不得重建未测镜像后直接推送。
- [ ] full commit SHA固定官方Actions；使用Buildx缓存与明确单平台，不能把linux/arm64当amd64验证；配置缺失清楚失败，不伪造publish/deploy成功。
- [ ] `git archive`相同SHA生成源码归档；OpenSSH StrictHostKeyChecking=yes，rsync至 ROOT/releases/SHA，不能在部署根 --delete，不再服务器git pull。源代码不在服务器编译。
- [ ] production部署串行且不取消运行中任务；递增运行序号交给Task4二次检查。手动重复同版本需明确支持或友好拒绝，不形成旧版本覆盖。
- [ ] actionlint检查workflow；本地package解包验证提交和实际产物匹配（不将文件字面结构固化为永久测试）；无Secrets不尝试真实推送或部署。
- [ ] 提交 `ci: add tested ACR images and SSH deployment workflow`。

### Task 6：朋友交接与最终验证

**Files:** 新增 deploy/{README.md,TROUBLESHOOTING.md}；修改根README和本计划完成证据。

- [ ] README按角色拆短流程：维护者配置ACR/GitHub一次；朋友只做域名/面板HTTPS、Docker登录、复制配置、setup/显式账号引导、面板反代/include和部署确认。不让朋友看Actions实现或安装Node/Python开发工具。
- [ ] 排错清单按症状：镜像认证/拉取失败、端口占用、目录权限、Nginx配置重复/未include、502、静态资源404、Host/Origin/CSRF、证书未续签、容器不健康、版本不匹配、备份/回退失败。每条提供诊断命令、正常输出和下一步。
- [ ] 写清默认28156、高位端口仍需检查；证书续签未实际验证，面板控制；不是CentOS7安装指南。数据下载、在线备份、停机明确恢复步骤和对应版本源码都给直接命令。
- [ ] 在/tmp临时库跑最终Docker两容器读写/重建/备份与隔离代理验证。运行相关已有全检查、frontend类型/构建、workflow/shell检查；通过后不无理由重复。
- [ ] 如Docker网络或工具不可用，调查具体阻碍并记录，不能用构建/YAML成功替代实际容器证据。仅清理自己的容器和临时目录；不启动朋友服务器流程。
- [ ] 独立最终代码审阅，修复重要问题；记录真实命令、实际结果与云端/面板/TLS未验证项。然后交付可审阅分支，不自动merge/push触发部署。
- [ ] 提交 `docs: add simple server setup and troubleshooting handoff`。

## 自检与执行方式

六项按顺序，接口由前项定义；Task1/2/4共享Origin和网络，Task2/3/4共享挂载和UID，Task4/5共享release契约，Task6使用实际命令。面板名称未确认不阻塞应用容器；通用全站反代/include说明和 NGINX_MANAGED=0保留少操作接法。用户最新指令已明确开始工作，按计划inline执行，最终由独立agent审阅。新架构变化仍向用户说明，执行不连接真实服务器。


## 交付记录

六项已实施，真实验证及范围见 [验证记录](../../verification/2026-10-07-compose-deployment.md)。Docker 权限问题已通过已安装的临时 rootless 引擎解决，未改变系统 Docker。实际容器链路、备份、临时 Nginx/TLS、失败恢复均已验证。

计划调整：当前只交付与朋友 x86_64 相符的 amd64 构建；Buildx 远程缓存暂未加入，以保留直接 build/load/test/push 的简短流程。部署目录加入 run_attempt，成功发布不被工作流重跑覆盖。首次工作流勾选 prepare_only，仅准备镜像和文件，显式初始化及面板准备之后再正式发布。

清单保留原始步骤作为审阅基线；云端 Actions、ACR/ECS、真实面板证书续签和浏览器验收未执行，不能由本地检查代替。分支保留供审阅，未自动 merge/push。
