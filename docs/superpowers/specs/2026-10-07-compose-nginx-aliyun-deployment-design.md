# Docker Compose、Nginx 与阿里云自动部署

日期：2026-10-07。状态：部署路线已讨论确认，本文待审阅，尚未实施。

## 1. 目标与用户确认

Vue 与 FastAPI 已完成最小前后端闭环。本阶段为它们提供 Docker 镜像、一个 Compose 文件、服务器目录中的持久化数据，以及 GitHub Actions 的测试、构建、推送和部署流程。

用户确认：

- 目标服务器是阿里云 ECS，需要配置公网与 HTTPS 入口。
- 镜像推送到用户已有的 ACR 个人版，实际地址以实例控制台为准。
- 保留服务器现有 Nginx，部署时更新本项目站点 conf。
- 使用经典 Certbot 自动申请与续签证书，不使用 Caddy。
- SQLite 与资产文件必须落在服务器可直接访问的目录，方便下载及备份。
- 阿里云镜像地址、网络配置、GitHub Variables/Secrets 和服务器配置要有操作文档。

实际域名、ECS 地址、区域、ACR 地址、部署账号与安装路径通过配置提供；设计与仓库不保存用户凭据。默认按 Linux/systemd、Docker Engine + Compose v2、已有主机 Nginx 设计。自定义 Nginx 安装路径作为安装参数，不替换它的全局配置。

## 2. 运行拓扑与选择

采用两个业务容器和现有主机入口：

```text
浏览器 HTTPS
  → ECS 现有 Nginx：TLS、ACME 验证目录、项目站点配置
    → 127.0.0.1:8080：frontend 容器（Nginx + Vue 构建产物）
      ├─ 静态文件
      └─ /api/* → backend:8001（FastAPI）
                         ↓
               服务器 bind mount 数据目录
```

frontend 的 Nginx 是镜像内的简单静态服务和 API 代理。主机 Nginx 是已有多站点公网入口。保留这两层使业务镜像可独立通过 Compose 启动，主机只需配置一个 loopback upstream；不要求迁移服务器上的其他站点。

相较将 Vue 产物解包到主机，本方案增加一个轻量运行容器，但前后端镜像可以按同一提交版本发布和回退。相较将公网 Nginx 整体迁入 Compose，本方案保留现有服务器管理边界。

Compose 只发布 frontend 到主机 loopback，默认 127.0.0.1:8080；backend 不发布宿主机端口。应用入口配置、镜像引用、数据目录和项目网络由同一个 Compose 文件管理。Certbot、主机 Nginx 和证书定时任务属于主机设施，不在每次应用升级中重建。

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
  acme/                     HTTP-01 验证目录
```

证书保留 Certbot 标准的 /etc/letsencrypt 目录，由主机 Certbot 管理，Nginx 引用其 live 路径。部署不复制私钥到镜像或 GitHub。

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

仓库保存主机站点模板和容器内配置，各自责任独立。主机只安装一个指定项目站点 conf；不覆盖 nginx.conf 或其他站点文件。

发布包携带与镜像相同提交版本的配置。服务器渲染域名、loopback 端口、ACME 根和证书路径，输入按格式严格校验，避免将任意文本注入 Nginx 或 shell。

使用 CI 传送的确定提交发布包，不在 ECS 运行 git pull 或 reset --hard 去追随最新 main；配置、镜像、脚本必须属于同一提交。同步范围只包含该版本发布文件，不能在整个部署根执行 rsync --delete。模板文件不能直接作为线上 conf 的软链接；安装的是经过渲染并校验的独立文件，避免同步模板时覆盖真实域名。

配置更新顺序：保留上一份项目配置 → 原子安装候选 conf → 对完整主机配置执行 nginx -t → 成功才 reload。校验失败时恢复原项目 conf，不 reload，部署报失败。reload 后进行有超时的 HTTP/HTTPS 验证；失败时恢复上一份项目配置与应用镜像，不修改业务数据。

不声称多个容器切换与 Nginx reload 具有跨进程事务原子性。部署状态记录前后端 digest、站点配置版本和完成状态；回退操作可定位上一份成功发布。首次部署没有上一版本时明确失败并保留数据及诊断，不伪造回滚成功。

## 7. Certbot 首次申请与自动续签

采用 certbot certonly --webroot，证书工具不自动重写受仓库管理的 Nginx conf。

首次安装由专用初始化步骤执行：

1. 校验域名 DNS 已指向目标 ECS，80/443 可达，服务器可访问证书机构的 ACME 服务。
2. 先安装仅 HTTP 的验证配置，服务 /.well-known/acme-challenge/；尚无证书时不引用不存在的 TLS 文件，也不开放 HTTP 登录。
3. 运行 Certbot webroot 申请该域名证书，使用明确证书名称和运维联系邮箱。
4. 安装 HTTPS 项目站点配置，nginx -t 后 reload，HTTP 保留验证目录并将普通页面重定向 HTTPS。
5. 配置主机 Certbot 自动续签任务；存在打包自带的 timer/cron 时复用并检查，不再增加重复调度。只有缺少调度时才安装项目提供的 systemd 定时任务。
6. 使用 deploy hook，仅证书实际成功更新后校验并 reload Nginx。hook 与应用配置更新使用同一服务器锁，避免并发 reload/配置切换。

后续每次推送只更新站点配置和应用版本，复用已有证书；不重复注册/强制申请。证书续签独立于 GitHub 推送频率。文档提供 certbot renew --dry-run、到期检查和失败日志定位方法。

## 8. GitHub Actions 流水线

三个阶段：

- CI：PR 和 main push 运行现有 Python/Node 检查、前端 typecheck/build、Compose 配置检查及实际容器 HTTP smoke；PR 阶段不读取部署 Secrets，不推送镜像、不部署。
- 发布：仅 main 的成功检查结果允许构建前后端镜像，通过容器 smoke 后推送到现有 ACR。测试的镜像与推送的镜像必须相同；若重新构建必须重新 smoke。固定提交 SHA，生成 digest 与该提交的发布包；可缓存构建层，不能把缓存成功当作测试证据。
- 部署：使用 GitHub production Environment 和 SSH 验证过的主机密钥，将发布包传到指定目录，服务器拉取两个确切 digest，应用健康后更新项目 Nginx conf，HTTPS 验证通过才记录发布成功。

生产部署串行执行，不能取消已经进入主机配置切换的任务。CI 新版本可取消旧的检查；部署需拒绝已过期的发布任务，防止旧构建后完成覆盖新版本。服务器也使用文件锁，协调部署、配置 reload 与续签 hook。

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
| 网站配置 | SITE_DOMAIN、ACME_EMAIL、项目 conf 路径、loopback 端口、网络 CIDR | 主机配置；非秘密值可从 Environment Variables 发布 |
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
