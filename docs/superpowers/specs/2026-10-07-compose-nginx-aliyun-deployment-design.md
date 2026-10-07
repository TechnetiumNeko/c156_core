# Docker Compose、Nginx 与阿里云自动部署

日期：2026-10-07。状态：用户已授权开始实施。按已提供组件版本复用服务器环境，TLS 交给面板；自动续签作为首次上线检查，不声称已经验证。

## 1. 目标与用户确认

Vue 与 FastAPI 已完成最小前后端闭环。本阶段为它们提供 Docker 镜像、一个 Compose 文件、服务器目录中的持久化数据，以及 GitHub Actions 的测试、构建、推送和部署流程。

用户确认：

- 目标服务器是阿里云 ECS，需要配置公网与 HTTPS 入口。
- 镜像推送到用户已有的 ACR 个人版，实际地址以实例控制台为准。
- 保留服务器现有 Nginx，部署时更新本项目站点 conf。
- 不使用 Caddy；原 Certbot 路线经后续讨论简化为复用服务器面板的证书管理，本阶段不安装 Certbot 或新的续签任务。
- SQLite 与资产文件必须落在服务器可直接访问的目录，方便下载及备份。
- 阿里云镜像地址、网络配置、GitHub Variables/Secrets 和服务器配置要有操作文档。
- 宿主机应用端口默认 127.0.0.1:28156，可配置；公网 Nginx 仍使用 80/443，容器内部端口不要求对应高位端口。
- 面向朋友服务器交付，尽量减少手工配置。GitHub/ACR 凭据配置由项目维护者完成，朋友只处理首次服务器准备、域名/证书及数据引导。
- 用户先前转述系统为 CentOS 7，随后提供实际版本输出：内核 6.6.47-12.oc9.x86_64，Docker 28.0.1-20241223130549-3b49deb，Compose 2.32.1，Nginx 1.26.3；主机没有 certbot 命令。按实际组件设计，不要求先升级现有服务或安装 Ubuntu apt/snap 工具。
- 用户说明服务器面板可以图形化配置证书，并同意继续实施。面板名称及续签能力尚未验证；按通用面板交接编写文档，首次上线须检查续签和 HTTPS，不能以“大概率支持”代替验收。

实际域名、服务器地址、区域、ACR 地址、部署账号与安装路径通过配置提供；设计与仓库不保存用户凭据。使用已提供的 Docker/Compose 和主机 Nginx；oc9 标识符合 OpenCloudOS 9，准确发行版名称以 /etc/os-release 为准，但当前不需要为应用部署变更系统。服务器面板可能使用自定义 Nginx 安装路径和 reload 入口，这些作为安装参数，不替换它的全局配置。组件版本是制定方案的依据，不能把本地容器通过称为朋友服务器已完成验收。

## 2. 运行拓扑与选择

采用两个业务容器和现有主机入口：

```text
浏览器 HTTPS
  → 服务器面板管理的现有 Nginx：TLS、项目站点入口
    → 127.0.0.1:28156：frontend 容器（Nginx + Vue 构建产物）
      ├─ 静态文件
      └─ /api/* → backend:8001（FastAPI）
                         ↓
               服务器 bind mount 数据目录
```

frontend 的 Nginx 是镜像内的简单静态服务和 API 代理。主机 Nginx 是已有多站点公网入口。保留这两层使业务镜像可独立通过 Compose 启动，主机只需配置一个 loopback upstream；不要求迁移服务器上的其他站点。

相较将 Vue 产物解包到主机，本方案增加一个轻量运行容器，但前后端镜像可以按同一提交版本发布和回退。相较将公网 Nginx 整体迁入 Compose，本方案保留现有服务器管理边界。

Compose 只发布 frontend 到主机 loopback，默认 127.0.0.1:28156；backend 不发布宿主机端口。高位端口仍需在首次准备时检测是否占用；改变端口时同步更新 Compose、项目反代片段与健康检查，不能自动跳到一个未记录的端口。应用入口配置、镜像引用、数据目录和项目网络由同一个 Compose 文件管理。公网 Nginx、证书和续签属于面板管理边界，不在应用升级中重建。

本阶段使用单 backend 实例、单 Uvicorn worker；升级允许短暂停顿，不承诺零停机。不引入 Kubernetes、Redis、云数据库或资产上传功能。

## 3. 镜像与配置

- backend 镜像安装固定的 Python 运行依赖，包含公开应用服务、管理命令和部署需要的工具，不包含运行库、旧样本数据、测试凭据或本机 .env。
- frontend 多阶段构建：Node/npm ci/build，产物复制到固定版本 Nginx 镜像；运行时无需 Node 或 Vite。
- 基础镜像与第三方 Actions 固定可追溯版本/摘要。前后端分别发布到 ACR 仓库，以完整 Git commit SHA 标记，部署记录使用构建产出的镜像 digest。
- ACR 个人实例优先推送单平台普通 image manifest；默认不附加 Buildx provenance/SBOM attestation，以兼容参考项目实际遇到的旧实例 manifest 拒绝问题。保留提交 SHA 与 digest 追踪，不把关闭 attestation 说成完成镜像签名或供应链证明。
- 默认构建平台为 linux/amd64。ARM ECS 通过构建平台变量明确选择，不能把 amd64 镜像当作 ARM 交付。
- Compose 本地构建与服务器拉取使用同一服务契约；生产通过显式镜像引用运行，服务器不执行 npm/pip 构建。
- 构建上下文使用 .dockerignore 排除 .git、.worktrees、.venv、node_modules、运行数据库、备份与秘密配置。
- 代码按完整应用包复制，依赖层与代码层分开。必须启动最终镜像检查模块可导入，避免裸机测试通过但 Docker COPY 漏模块。
- Compose 的 .env 插值与注入应用环境分开处理；backend 的 environment 显式列出生产参数，不能假设 Compose 自动把主机 .env 注入容器。镜像/SSH 登录凭据不注入应用进程。

## 4. 主机数据目录与备份

默认部署根为 /srv/c156，可配置为其他绝对目录：

```text
/srv/c156/
  compose.yaml
  .env                      主机配置，不进 Git
  releases/<commit>/        发布配置与部署元数据
  data/                     SQLite 与 WAL/SHM 所在目录
    c156.sqlite
  assets/                   资产持久化目录
  backups/                  可下载的一致数据库备份
  nginx/proxy.inc           项目反向代理指令片段
```

证书保留面板管理的目录和站点引用路径。部署不复制私钥到镜像或 GitHub，不假设证书位于 /etc/letsencrypt。

数据使用 bind mount，不放在容器 writable layer 或仅可从 Docker 内部定位的匿名卷。挂载整个数据库目录，包含 SQLite 必需的 WAL/SHM。资产挂载不代表提供上传接口或公开下载所有文件；本阶段不将该目录直接暴露为公开 Nginx 静态目录。

首次初始化和 bootstrap-admin 仍是显式操作，通过 backend 镜像中的现有管理命令进行；密码交互输入。不在正常启动或每次部署中自动初始化、升级、清空或重置管理员。

提供独立备份命令，使用 SQLite Online Backup API 生成一致快照，放入 backups 后供下载；活跃 WAL 库不以直接复制单个 .sqlite 文件作为备份。备份输出使用限制性权限，目录所有权须兼顾非 root 应用用户和指定运维账号。恢复是明确停服务、还原指定快照并验证 schema/WAL 后再启动的人工操作，不由部署失败自动执行。备份文件可能使用非 WAL 模式，恢复步骤必须在协议校验成功后通过既有 Database.configure_runtime 显式完成 WAL 配置，不能仅复制文件就宣称可运行。

资产目录可独立归档；当前没有媒体写入/数据库关联模型，不能宣称数据库和资产具备未来媒体系统的事务一致快照。文档说明这一边界。

## 5. 生产入口与代理信任

增加显式生产配置；本地入口继续默认 loopback，维持现有开发行为。生产容器可监听 0.0.0.0:8001，但后端端口仅存在于 Compose 网络。

- 必须显式配置公开域名、https Origin 和 Secure Cookie；不能以开放 CORS、通配 Host 或移除 CSRF 解决代理问题。
- 外层 Nginx 保留合法公开 Host 和 Origin，覆盖访客提供的来源/协议转发头；内层代理传递经过规范化的客户端地址。原有 JSON/请求体、Cookie 和错误净化规则保留。
- 生产代理信任仅限项目代理所在的 Compose 网络。Compose 子网与信任 CIDR 配置一致，默认项目子网 172.30.156.0/24，可在服务器安装时调整以避免冲突；不默认信任整个公网或任意转发头。
- 登录限流继续按真实客户端来源工作，不能把所有用户都归入代理容器的同一个来源桶。客户端自行伪造 X-Forwarded-For 不得改变公网入口记录的实际来源。
- 主机 loopback 应用端口不对公网开放。访问服务器/Docker 管理权限的人员属于运维信任边界。
- 2 MiB JSON 限制在应用层继续执行；两层 Nginx 的请求体限制相容，不允许默认 1 MiB 代理上限意外缩小已交付契约。

提供不返回用户/目录/凭据的 readiness 接口，检查服务已启动且数据库可读。Compose healthcheck、部署验证均使用该接口，不使用写入用户正文作为生产健康检查。公网静态首页和 API 都需要检查，不能只凭容器 running 判定部署成功。

## 6. Nginx 配置更新

仓库保存主机项目反代片段模板和容器内配置，各自责任独立。公网站点 server/TLS/ACME 块由面板管理；CI 仅安装项目目录中的独立 proxy.inc，不覆盖整份站点 conf、nginx.conf 或其他站点文件。

发布包携带与镜像相同提交版本的配置。服务器渲染 loopback 端口及代理参数，输入按格式严格校验，避免将任意文本注入 Nginx 或 shell。proxy.inc 只含 location 内的代理指令，不包含 server/location/SSL 块；朋友在面板创建全站反代后一次性将其包含到该反代 location。面板若不支持 include，可直接使用面板全站反代配置，配置 NGINX_MANAGED=0，日常部署不改 Nginx；默认示例说明 include 接法及检查方法。

使用 CI 传送的确定提交发布包，不在 ECS 运行 git pull 或 reset --hard 去追随最新 main；配置、镜像、脚本必须属于同一提交。同步范围只包含该版本发布文件，不能在整个部署根执行 rsync --delete。模板文件不能直接作为线上 conf 的软链接；安装的是经过渲染并校验的独立文件，避免同步模板时覆盖真实域名。

用户关心服务器是否需要保留代码以便应急：GitHub Actions 检出并构建确切提交，同时将该提交的已跟踪源码归档 source.tar.gz 随发布包送到服务器。源码归档供查看和排查，不参与正常启动，不包含 .git、运行数据或未跟踪凭据。服务器因此有对应版本代码，但正常自动部署不依赖它再访问 GitHub，也不额外要求配置 VPS→GitHub 密钥。文档提供按明确 SHA 手动获取源码的可选应急方法。

配置更新顺序：保留上一份项目配置 → 原子安装候选 conf → 对完整主机配置执行 nginx -t → 成功才 reload。校验失败时恢复原项目 conf，不 reload，部署报失败。reload 后进行有超时的 HTTP/HTTPS 验证；失败时恢复上一份项目配置与应用镜像，不修改业务数据。

不声称多个容器切换与 Nginx reload 具有跨进程事务原子性。部署状态记录前后端 digest、站点配置版本和完成状态；回退操作可定位上一份成功发布。首次部署没有上一版本时明确失败并保留数据及诊断，不伪造回滚成功。

## 7. 面板证书与首次上线

朋友在现有面板中创建网站、绑定域名、申请/配置证书并开启 HTTPS 和自动续签，创建全站反代至 http://127.0.0.1:28156。项目部署保留面板的 TLS、ACME 和证书文件，不运行 Certbot，不增加并行续签服务。

提供简短首次上线检查：域名解析正确、HTTPS 证书有效、面板自动续签已启用、/ 与 /assets/ 和 /api/ 均到达项目容器。手工上传证书不等同自动续签；续签失败按面板日志处理。项目文件锁只协调项目部署和 rollback，不能声称控制未知面板的内部调度。

如果面板证书管理不能满足需求，再由用户明确选择 Certbot 补充方案；当前交付不为这一假设增加组件和维护步骤。

## 8. GitHub Actions 流水线

三个阶段：

- CI：PR 和 main push 运行现有 Python/Node 检查、前端 typecheck/build、Compose 配置检查及实际容器 HTTP smoke；PR 阶段不读取部署 Secrets，不推送镜像、不部署。
- 发布：仅 main 的成功检查结果允许构建前后端镜像，通过容器 smoke 后推送到现有 ACR。测试的镜像与推送的镜像必须相同；若重新构建必须重新 smoke。固定提交 SHA，生成 digest 与该提交的发布包；可缓存构建层，不能把缓存成功当作测试证据。
- 部署：使用 GitHub production Environment 和 SSH 验证过的主机密钥，将发布包传到指定目录，服务器拉取两个确切 digest，应用健康后更新项目 Nginx conf，HTTPS 验证通过才记录发布成功。

生产部署串行执行，不能取消已经进入主机配置切换的任务。CI 新版本可取消旧的检查；部署需拒绝已过期的发布任务，防止旧构建后完成覆盖新版本。服务器使用文件锁协调项目部署和回退；面板外部操作在运维时避免并发进行。

使用 OpenSSH/rsync 传送发布包并运行脚本，严格校验预配置的 known_hosts。各 job 设置合理超时，workflow 默认 contents:read；ACR 登录使用专用凭据，不复制参考项目为 GHCR 双推设置的额外 GitHub packages 权限。第三方 Actions 固定完整 commit SHA，版本在实施时核对官方发行记录。

初始化证书/账号与日常部署分别提供操作入口；普通 main push 不偷偷申请管理员或修改现有数据库。首次真实上线需完成主机配置及数据引导，缺失配置时给出明确失败信息。未提供 Secrets 时不能把发布或部署标记为成功。

本阶段交付并本地验证 Docker/Compose、脚本和 workflow；实际 ACR 推送、ECS 部署与公网证书申请须在用户配置账号/域名并授权启动后执行，不把静态 YAML 检查称为云端部署验证。

## 9. Variables、Secrets 与阿里云文档

| 类别 | 配置 | 位置 |
| --- | --- | --- |
| 镜像地址 | ACR_REGISTRY、ACR_NAMESPACE、前后端仓库名称、构建平台 | GitHub Variables |
| 镜像推送认证 | ACR_USERNAME、ACR_PASSWORD | GitHub Secrets，注册表用户名/密码，不是阿里云主账户登录密码 |
| 部署目标 | DEPLOY_HOST、DEPLOY_PORT、DEPLOY_USER、DEPLOY_ROOT | production Environment Variables |
| SSH | DEPLOY_SSH_KEY、DEPLOY_KNOWN_HOSTS | production Environment Secrets，可信主机密钥从独立渠道确认 |
| 网站配置 | SITE_DOMAIN、项目 include 路径、loopback 端口、网络 CIDR | 一份主机配置，非秘密值；证书只在面板配置 |
| 拉取镜像 | 服务器 Docker 登录凭据 | 首次主机配置，优先只读凭据，不写入镜像或站点 conf |

HTTP webroot 验证无需 AliDNS API 凭据。本阶段无需额外阿里云 AccessKey；不为了已有镜像仓库登录额外申请高权限云 API 密钥。

部署文档必须说明：

- 从已有 ACR 实例控制台复制真实登录地址，兼容新版个人实例域名，不硬编码旧 registry.cn-*.aliyuncs.com。
- 两个镜像仓库的创建、登录、推送/拉取权限及网络可达性；已有个人版作为用户选择的目标，注明官方个人版开发测试定位与无 SLA 的限制，未来换实例只改镜像地址/凭据。
- GitHub 构建机默认使用官方 Node/Python/基础镜像来源；ECS 日常只从 ACR 拉取应用镜像，减少现场依赖安装。可选本地构建镜像站单独列出，不混同应用 registry。
- 阿里云 Docker Hub 加速器已停止同步最新镜像且限 ECS 访问，不能默认要求 GitHub hosted runner 使用这个地址。
- 域名解析、公网端口、证书机构连通性；涉及中国内地服务器的域名上线条件链接官方说明，由实际服务器区域决定。
- SSH 与主机权限、Docker 管理权限边界、自定义 Nginx 路径、首次引导、正常部署、失败回退、备份下载及明确恢复步骤。

不要求用户将私钥、密码或其他秘密粘贴到聊天或提交到 Git。

### 朋友服务器的最简交接

- 提供 deploy/README.md：按顺序执行的首次准备、配置、启动、初始化管理员、确认成功和后续更新步骤；必要步骤与可选配置分开。
- 提供 deploy/TROUBLESHOOTING.md：按实际报错/症状索引，给出要执行的诊断命令、预期现象和下一步，不要求先理解项目架构。
- 一份服务器配置文件，必填项集中；宿主机端口、目录、内部网络及应用 UID/GID有合理默认值，首次准备明确检测或生成，不让使用者在多处重复填写。
- 一条共用部署入口供 CI 与人工更新；首次准备是独立入口，不让日常部署执行初始化、软件安装或改写其他站点。
- 复用已提供的 Docker 28、Compose 2.32.1、Nginx 1.26.3；不要求安装 Certbot，不以一键脚本覆盖现有 Nginx 安装和配置。
- 使用已提供版本的服务器准备说明，先复用现有 Docker/Compose/Nginx。保留已有可用组件，不自动升级系统/内核、替换全局软件源、关闭 SELinux/firewalld 或停止其他站点。面板若管理 TLS，部署不能覆盖面板维护的证书和站点块；可采用首次添加项目反代 include 的方式，由 CI 只更新该独立片段。具体接法待面板能力确认，不让朋友重复维护 Certbot 与面板两套证书。
- 发布时一并留存确切提交源码与版本记录；朋友不必在服务器安装 Node/Python 开发工具链或掌握镜像构建流程。

## 10. 验证范围

- 使用临时数据库与临时宿主机 bind mount 构建/启动 Compose，验证 frontend → backend 登录、读写、退出，重建容器后内容仍在。
- 验证生产配置保留 Host/Origin/CSRF/Secure Cookie，真实来源通过受信代理链传递，伪造访客头不能绕过来源限流。
- 备份测试调用真实 SQLite backup API，包含未 checkpoint 的 WAL 写入；恢复到独立路径，验证正文与协议/WAL 可用。
- 对部署脚本新增简短的关键失败行为测试：nginx -t 失败恢复、健康失败回退、首次部署无旧版本、过期任务拒绝。复用生产脚本，不能只断言源码字符串或 mock 调用顺序。
- 使用隔离临时 Nginx 配置执行真实 nginx -t，不更新当前开发机器/服务器的线上站点；Certbot production 申请不用于自动化测试。
- 运行与改动相关的既有 Python/Node 检查与前端构建；workflow 用对应检查工具验证语法/引用，容器 smoke 才提供运行证据。
- 不执行浏览器自动点击或填写表单，不做无关前端样式修改。结束测试自己启动的容器/进程，保留用户数据。
- 交付明确区分本地已验证、需要真实域名/Secrets 的云端未验证项和后续上线条件。

## 11. 一手资料

- Nginx reload：https://nginx.org/en/docs/beginners_guide.html
- Certbot webroot/续签/deploy hook：https://eff-certbot.readthedocs.io/en/stable/using.html
- Compose bind mount：https://docs.docker.com/engine/storage/bind-mounts/
- SQLite 在线备份：https://www.sqlite.org/backup.html
- ACR 个人实例推送/拉取：https://help.aliyun.com/zh/acr/user-guide/use-a-container-registry-personal-edition-instance-to-push-and-pull-images
- ACR 个人版范围：https://help.aliyun.com/zh/acr/user-guide/create-a-container-registry-personal-edition-instance
- 阿里云镜像加速器：https://help.aliyun.com/zh/acr/user-guide/accelerate-the-pulls-of-docker-official-images
- GitHub Environments：https://docs.github.com/en/actions/concepts/workflows-and-actions/deployment-environments
- Uvicorn 代理配置：https://uvicorn.dev/settings/
- Docker 构建后测试再推送：https://docs.docker.com/build/ci/github-actions/test-before-push/
- GitHub 部署并发：https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency
- 阿里云域名上线前期条件：https://help.aliyun.com/zh/icp-filing/basic-icp-service/user-guide/overview/
- CentOS 7 生命周期：https://www.centos.org/centos-linux/
- Docker 当前 CentOS 支持范围：https://docs.docker.com/engine/install/centos/
- Certbot 安装方式：https://eff-certbot.readthedocs.io/en/stable/install.html
- OpenCloudOS 9.2 与 6.6 内核：https://docs.opencloudos.org/release/v9.2/

## 12. 参考已有项目（2026-10-07 只读检查）

参考路径是同级 www 目录中的 TouhouCCB 与 BumperWar。本次只读取 Dockerfile、Compose、workflow、部署脚本及指南，没有修改参考项目或读取其真实 .env/凭据。

| 参考实现 | 本项目采用或调整 |
| --- | --- |
| TouhouCCB 的 backend/Dockerfile、docker-compose.yml：依赖层缓存、非 root、loopback 端口、宿主机目录、UID/GID | 沿用这些部署原则；数据库保持 SQLite，不移植 PostgreSQL/Alembic 或自动建库逻辑 |
| TouhouCCB 的 .github/workflows/ci.yml：main 发布、ACR、SHA 标签、Buildx 缓存、CI rsync 配置包、生产部署不取消 | CI 传送确定版本配置，ECS 无需连接 GitHub；前后端使用同提交 SHA 与实际 digest，串行部署并拒绝过期任务 |
| TouhouCCB 的 deploy/nginx.conf：线上软链指向模板曾被同步覆盖域名的记录 | 安装渲染后的独立项目 conf，不将线上站点直接链接到待同步模板 |
| TouhouCCB 的 Buildx/ACR 注释：默认 OCI attestation 曾被实例拒绝 | 默认普通单平台 manifest，文档记录兼容策略；不复制 GHCR 双推，本次用户只要求现有 ACR |
| BumperWar 的 Dockerfile：缺少拆分模块 COPY 导致实际 crashloop 的记录 | 最终镜像必须经过启动与 HTTP smoke，不能仅靠裸机测试或 docker build |
| BumperWar 的 Compose：loopback、环境必须显式注入、数据必须持久化 | 应用参数显式 environment；按用户要求采用服务器 bind mount，替代其 named volume |
| BumperWar 的 deploy/deploy.sh：健康检查、项目 conf 备份、nginx -t、reload | 沿用校验和回退流程；本站 conf 更新失败属于本次部署失败，不降级成最终成功提示，不清空旧站点 conf 掩盖错误 |
| BumperWar 的服务器 git reset 与 latest 默认镜像 | 改为确定提交发布包和 digest；不硬重置服务器工作目录、不用可变 latest 决定生产版本 |
| 两项目的 deploy 入口与 ACR_*/DEPLOY_* 命名 | CI 与手动部署共用生产脚本，配置名称尽量一致；按本项目表格区分非秘密 Variables 与真正 Secrets |

参考项目中的业务限流、ESA、游戏导出、SSO、数据库迁移和赛季操作均不属于本任务范围。

## 后续确认：test/prod 环境划分

用户后续将自己的 Ubuntu ECS 定为 test、朋友服务器定为 prod，并确认 main 自动发布 test，prod 仅手动选择发布。此确认替代正文中单个 production Environment 及 Repository 级 ACR 配置的描述。

各环境分别设置所有 ACR、SSH Variables/Secrets，参数名相同，值各自独立；DEPLOY_ENVIRONMENT 必须匹配所选环境。旧 Repository 同名配置应迁移并删除，以避免 GitHub 上层值继承。服务器 config.env、数据、证书和发布状态分别保留在各自主机。

同一次 workflow 构建并 smoke 后，通过 artifact 将已测试镜像交给选定环境发布，不在部署任务重新构建。prod 手动运行使用该次 main 提交，维护者在发布前确认该提交已在 test 验证。artifact 使用生产者输出的 ID 下载，兼容仅重跑失败的部署任务；两个环境使用独立的部署并发组。

## 最新确认：Repository 前缀配置

上游是个人仓库，当前协作者能配置 Repository Secrets/Variables，但不能配置 GitHub Environments。用户明确选择 Repository 方案。此确认替代前述 Environment 级配置方案；触发规则仍为 main 自动 test、prod 手动选择。

所有部署参数在 Repository 中按 TEST_／PROD_ 前缀存储，工作流根据选定目标索引对应名称，不使用 GitHub Environment，不读取旧无前缀项，不在 PROD_ 缺失时借用 TEST_。因此不再需要 DEPLOY_ENVIRONMENT 标记。两组凭据属于仓库统一管理范围，前缀用于分组，不提供 Environment 的审批或访问隔离。具体名称以 deploy/MAINTAINER.md 为准。
