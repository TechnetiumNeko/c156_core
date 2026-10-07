# 报错排查清单

从部署手册的失败链接进入对应编号。**先修当前这一步，再继续下一步。** 不要删 data、重置账号或停掉其他网站。

| 卡在哪 | 看哪里 |
| --- | --- |
| 第 1 步找不到文件 | [E01](#e01) |
| Docker、命令或目录权限报错 | [E02](#e02) |
| 配置错误、端口占用、网络重叠 | [E03](#e03) |
| ACR 登录／推送／拉取失败 | [E04](#e04) |
| GitHub 连不上服务器 | [E05](#e05) |
| Actions 不运行／失败，images.env 不存在 | [E06](#e06) |
| 创建数据库或管理员失败 | [E07](#e07) |
| Nginx 保存／校验／重载失败 | [E08](#e08) |
| 域名打不开、证书警告／过期 | [E09](#e09) |
| 正式发布后 502、容器不健康 | [E10](#e10) |
| API 403，或登录后仍未登录 | [E11](#e11) |
| SHA 不一致，或 stale deployment | [E12](#e12) |
| 备份／回退失败、磁盘满 | [E13](#e13) |

<a id="e01"></a>

## E01：找不到 setup.sh

**看到：** git clone 失败，或 ~/c156/deploy/setup.sh 不存在。

| 现象 | 怎么处理 |
| --- | --- |
| git: command not found | 让服务器管理员安装 Git，再克隆 |
| GitHub 连接超时 | 确认服务器能访问 GitHub，恢复网络后重试；后续自动更新也需要服务器能够访问 GitHub |
| destination path already exists | 如果目录内已有 .git 和 deploy/setup.sh，直接使用；若含现有数据或其他项目，不删除，交给维护者确认目录 |
| 已克隆但没有 deploy/setup.sh | 确认上游 main 已合并部署代码，克隆了正确仓库；不要只下载一个脚本 |

**修好后：** `ls "$HOME/c156/deploy/setup.sh"` 显示文件，回手册第 1 步。

<a id="e02"></a>

## E02：命令缺失或权限不足

| 看到什么 | 怎么处理 |
| --- | --- |
| `./deploy/setup.sh: Permission denied` | 先用 bash deploy/setup.sh；git ls-files -s deploy/setup.sh 显示 100644 时是脚本没有执行位，不代表所有者错误 |
| Docker socket `permission denied`／`Cannot connect` | 确认使用维护者填写的 DEPLOY_USER；让管理员检查 Docker 服务及该账号权限 |
| `docker: command not found`／找不到 Compose | 请管理员检查已有安装和 PATH，不重新覆盖现有 Docker |
| `missing command`／第 1 步少了路径 | 请管理员补齐对应命令；服务器需要 Git，但无需 rsync |
| mkdir、chown、目录 `Permission denied` | 该账号需要操作 /home/deploy/c156 和设置目录所有者；交给服务器管理员处理 |
| 数据目录仍 permission denied | 核对 APP_UID/GID 和目录所有者；若是 SELinux 拒绝，为专用数据目录配置容器标签，不关闭 SELinux |

文件由执行 git clone 的账号创建。检查实际账号、目录所有者及容器身份：

```bash
whoami
id
ls -ld "$HOME/c156" "$HOME/c156/data" "$HOME/c156/assets" "$HOME/c156/backups"
```

新配置的 APP_UID/GID 留空时，setup 自动记录部署账号的 UID/GID，目录保持 700。若已复制旧配置的 10001，而 setup 尚未执行，把 APP_UID/GID 清空后运行 setup 即可。明确使用其他 UID/GID 时，请管理员保证容器能写入目录、部署账号能查看数据库是否存在及下载备份。不要给整个仓库改所有者，也不要用 chmod 777。

**修好后：** 回到报错的那条命令重新运行；普通 setup 可以重跑，`--init-db` 先看 [E07](#e07)。

<a id="e03"></a>

## E03：配置、端口或 Docker 网络

**打开 setup 输出的 config.env，按错误处理（deploy 用户默认 /home/deploy/c156）：**

| 看到什么 | 怎么改 |
| --- | --- |
| `set your real SITE_DOMAIN first`／`invalid SITE_DOMAIN` | 填真实域名，例如 `docs.example.cn`，不带协议、斜杠、引号或空格 |
| `unknown config key`／`config must use KEY=value` | 对照交付的 config.env.example；一行一个 KEY=value，不写 shell 命令 |
| `DEPLOY_ROOT differs`／bind source path does not exist | 默认用部署用户的家目录/c156；setup 自动写实际路径。自定义时 GitHub、config.env 和命令路径须一致 |
| `port … is occupied`／`address already in use` | 选择未使用的高位端口，修改 APP_PORT，不停其他服务 |
| `invalid APP_PORT` | 只填 1024～65535 内的整数，例如 `28157` |
| `address pool overlaps` | 让维护者选一个未被 Docker 占用的私有 IPv4 网段，修改 PROXY_NETWORK；配置会同时传给 Compose 与后端 |

例如检查 28157 是否空闲：

```bash
ss -H -ltn 'sport = :28157'
```

**正常结果：** 没有输出，表示当前没有 TCP 监听占用该端口。再改配置、重跑普通 setup。若使用 `NGINX_MANAGED=0`，面板代理端口也要跟着改；默认项目 include 会在正式发布时更新。

<a id="e04"></a>

## E04：ACR 登录或镜像失败

| 报错 | 定位与处理 |
| --- | --- |
| `unauthorized`／`denied` | 核对 ACR 控制台域名、用户名、ACR 登录密码；确认命名空间和两个仓库已创建 |
| 服务器能登录，Actions 仍登录失败 | 维护者检查 Repository 中本次目标的 TEST_ACR_* 或 PROD_ACR_*，核对 Secrets／Variables 类型 |
| Actions 能推送，服务器拉取 denied | 公开仓库先确认两个仓库都公开且地址正确；私有仓库用 DEPLOY_USER 在服务器 docker login，不同用户不共用登录状态 |
| DNS、连接超时 | 核对实际公网 registry 域名和服务器网络；仅 VPC 可达的地址不能直接给 GitHub runner 使用 |
| `manifest unknown`／不支持媒体类型 | 维护者查看 push 日志及镜像 digest；保持单平台 amd64、关闭 provenance/SBOM，不手改 images.env |

**修好后：** 私有仓库登录需显示 `Login Succeeded`；公开仓库匿名拉取时无需登录。prepare 失败时，由维护者重新准备发布，直到三个 job 变绿，再确认 `/home/deploy/c156/images.env` 存在。

<a id="e05"></a>

## E05：Actions 的 SSH 或源码更新失败

创建／查看专用部署钥匙，见 [SSH 操作步骤](SSH.md)。这部分交给维护者处理，朋友提供 Actions 失败日志即可。

| 报错 | 维护者检查 |
| --- | --- |
| `Permission denied (publickey)` | Repository 中本次目标的 TEST_DEPLOY_USER／TEST_DEPLOY_SSH_KEY 或 PROD_ 对应项，以及公钥是否装在该账号 authorized_keys |
| `Connection timed out`／`refused` | 对应 TEST_DEPLOY_HOST／PROD_DEPLOY_HOST Secret、端口 Variable、SSH 服务、安全组和防火墙 |
| git fetch 连接失败 | 服务器需能访问 GitHub；检查 DNS 和网络后重试 |
| `tracked files have local changes` | 运行 git status，交给维护者保存并处理本地修改；不执行 git reset --hard |
| mkdir `Permission denied`／`No such file` | 先让朋友完成 setup，确认 /home/deploy/c156 是克隆的仓库且 DEPLOY_USER 可写 |

**修好后：** 重新运行相应 workflow。源码由 Actions 自动 git fetch 并检出准确提交，不需要手动 git pull。

<a id="e06"></a>

## E06：Actions 或镜像准备不正常

**维护者打开本次 workflow，先看哪个 job 红了：**

| 位置／现象 | 下一步 |
| --- | --- |
| 看不到 workflow 或 Run workflow | 确认部署代码已在 main，仓库允许 Actions；手册要求在 main 手动运行 |
| deploy 被跳过 | PR 不发布，手动分支应选 main；推送 main 的目标固定为 test |
| 日志显示 Target not enabled／Deployment disabled | 在 Repository Variables 设置本目标的 `TEST_DEPLOY_ENABLED=true` 或 `PROD_DEPLOY_ENABLED=true` |
| 缺少 TEST_DEPLOY_HOST／PROD_DEPLOY_HOST | 地址须填 Repository Secrets，放在 Variables 不会被读取；名称与目标对应 |
| Missing Repository parameter | 按日志中的完整 TEST_／PROD_ 名称补齐；服务器地址和凭据填 Secrets，其他参数按配置表填 Variables，不能省略前缀 |
| 意外等待 Environment 审批或仍提示 DEPLOY_ENVIRONMENT | 正在运行旧工作流；确认 main 已合并 Repository 方案，再 Run workflow 开新运行 |
| checks 红了 | 打开第一个失败步骤，交给维护者修代码；不跳过检查强行部署 |
| images 构建或冒烟红了 | 维护者查看构建／容器日志；认证与拉取问题查 [E04](#e04) |
| deploy 的 SSH／git fetch 红了 | 查 [E05](#e05) |
| deploy 的数据库／Nginx／健康／HTTPS 检查红了 | 按日志关键词查 [E07](#e07)、[E08](#e08)、[E10](#e10)、[E09](#e09) |
| 三个 job 绿了，但没有 images.env | 确认该次勾选 prepare_only、target_environment 选对服务器，并核对该目标的实际目录；未配置 DEPLOY_ROOT 时为 SSH 用户家目录/c156 |
| 正式发布时提示数据库缺失 | 第 4 步尚未完成；新库先显式初始化，已有库不要重建 |

**准备阶段的成功标志：** 日志有 `Images prepared`，服务器有 `/home/deploy/c156/images.env`。

**正式发布的成功标志：** 日志最后有 `Deployed …`，两个 HTTPS 检查返回同一个 SHA。绿色 prepare 不代表网站已经上线。

<a id="e07"></a>

## E07：初始化或管理员创建失败

**先区分是哪种情况：**

| 现象 | 处理 |
| --- | --- |
| `images.env missing` | 返回手册第 3 步，先成功运行 prepare |
| `database already exists; refusing initialization` | 已有库，不能再次 --init-db；不要删除。确认是否只是管理员创建中断 |
| 两次密码不一致／不符合密码要求 | 库可能已创建，按下面命令单独重试管理员引导 |
| `bootstrap requires an empty unowned library` | 库中已有账号或 owner；停止引导，使用已有账号，必要时让维护者处理账号恢复 |
| 数据库版本或 schema 错误 | 先备份现有数据，让维护者核对版本；不靠 init 自动升级 |

**仅当新库已创建、首个管理员尚未创建时，在服务器运行：**

```bash
bash -c 'source "$HOME/c156/deploy/common.sh"; load_config "$HOME/c156"; compose_for "$HOME/c156" run --rm --no-deps backend python -m src.identity bootstrap-admin --database /data/c156.sqlite --login-name admin --display-name 管理员'
```

输入两次网站密码，命令成功结束后继续手册第 5 步。引导会拒绝改写已有账号。

如果已经导入旧库，不要运行新库引导，联系维护者确认已有账号和数据是否可用。

<a id="e08"></a>

## E08：Nginx 保存、校验或重载失败

| 报错 | 怎么修 |
| --- | --- |
| `nginx binary not found` | 在面板确认真实 Nginx binary 路径，填写 config.env 的 NGINX_BIN |
| `duplicate location`／`duplicate proxy_pass` | 只保留一个 location /，其中只用一套代理设置；include 与面板生成的 proxy_pass 不同时保留 |
| include 文件不存在 | 确认 setup 成功，并且 /home/deploy/c156/nginx/proxy.inc 存在；不要直接 include 模板 |
| nginx -t 的文件名和行号报错 | 打开该配置定位到对应行，修好后让面板重新校验；不跳过 -t |
| reload `Permission denied` | DEPLOY_USER 需要操作该 Nginx；请管理员处理权限，并确认它使用的是面板同一配置 |

面板不允许这种 include 接法时，改用手册的[面板自管代理](README.md#panel-proxy)。不要替换整个站点配置，保留证书与 ACME location。

**修好后：** 面板保存、校验和重载都成功，再继续。正式部署中 Nginx 失败会停止，不自动回退。修好后重新发布。

<a id="e09"></a>

## E09：域名或 HTTPS 失败

将 YOUR_DOMAIN 换成真实域名，在服务器运行：

```bash
getent hosts YOUR_DOMAIN
curl -Iv --connect-timeout 5 --max-time 15 https://YOUR_DOMAIN
```

**正常结果：** DNS 指向正确的服务器／已配置入口；curl 能建立连接、证书校验通过。首次第 5 步还没正式启动应用时，HTTP 502 不代表证书失败。

| 现象 | 检查 |
| --- | --- |
| DNS 无结果／指向旧 IP | 修域名 A 记录，等解析更新后再查 |
| 连接超时或 refused | 检查 80/443 的安全组、主机防火墙和面板监听 |
| 浏览器证书警告／curl certificate 错误 | 面板证书是否包含该域名、是否过期、证书链是否完整 |
| 访问到其他网站／出现 HTTP 跳转循环 | 检查面板域名绑定及 HTTPS 跳转规则 |
| `public HTTPS … check failed` | 不加 `-k` 绕过证书；修复证书、域名或代理，再重新发布 |
| 证书过期 | 在面板续签并确认自动续签任务、ACME 验证入口、DNS 和 80/443 正常 |

项目不另起 Certbot。证书刚申请成功也要确认后续续签由谁执行。

<a id="e10"></a>

## E10：正式发布后 502 或容器不健康

**先在服务器同一个终端复制运行这一段：**

```bash
cd "$HOME/c156"
source deploy/common.sh
load_config "$PWD"
compose_for "$PWD" ps
compose_for "$PWD" logs --tail 100 backend frontend
```

**正常结果：** backend、frontend 都在运行且 healthy；日志没有反复启动失败。

| 日志内容 | 下一步 |
| --- | --- |
| 数据库缺失／初始化提示 | [E07](#e07)，不自动建库 |
| permission denied | [E02](#e02)，检查挂载目录和 UID/GID |
| image／manifest／pull 错误 | [E04](#e04) |
| address pool overlaps／端口占用 | [E03](#e03) |
| frontend healthy，但公网 502 | [E08](#e08)，核对面板代理目标与 APP_PORT 一致 |
| `exec format error` | 维护者核对主机架构与 amd64 镜像匹配 |
| 其他应用异常 | 把 backend 日志交给维护者，不只是重启 Nginx |

images.env 记录最近一次尝试的镜像，发布失败时容器可能仍在运行旧镜像；结合 Actions 日志及两个 HTTPS SHA 判断现场。

<a id="e11"></a>

## E11：API 403 或登录不保持

1. 用 config.env 的 SITE_DOMAIN 打开 **HTTPS** 网站，不使用 IP 或 HTTP 登录。
2. 核对面板代理保留 Host、Origin，并覆盖 X-Forwarded-For、X-Forwarded-Proto。设置见手册第 5 步。
3. 修改代理后重新加载页面，再登录；不要关闭 CSRF 校验或去掉 Secure Cookie。
4. 用户名为 `admin`，密码是第 4 步设置的网站密码，不是 ACR 密码。若登录明确返回 401，先核对账号密码。

如果页面资源 404，先看 [E12](#e12) 的前后端版本；清除该站点的旧浏览器缓存后重试。

**修好后：** 可以登录并保持会话；已有文档可读取、保存。仍失败时，将请求状态码和 backend 日志交给维护者。

<a id="e12"></a>

## E12：版本不一致或旧发布被拒绝

在服务器运行（替换域名）：

```bash
curl -fsS https://YOUR_DOMAIN/build-info.json
curl -fsS https://YOUR_DOMAIN/api/healthz
```

**正常结果：** 两条 build_sha 一致，并等于本次 Actions 的提交 SHA；healthz 的 status 为 ok。只比较两条相等不足以证明本次已更新。

| 现象 | 处理 |
| --- | --- |
| 两个 SHA 不同／都还是旧版本 | 查 Actions 是否正式 deploy 成功；检查域名是否到正确主机、代理是否到正确端口、是否有旧缓存 |
| build-info 正常，healthz 失败 | [E10](#e10) 或 [E11](#e11) |
| 前端静态资源 404 | 核对本次 frontend 镜像与 SHA，维护者查看构建日志；清理站点旧浏览器缓存 |
| `stale deployment rejected` | 旧任务晚到被拒绝；从 Run workflow 发起新一次运行，不改 .deploy-sequence 文件 |

已成功发布后，要重新发布请用 **Run workflow** 开新运行并选正确的 test／prod；需要恢复旧版本时按 [E13](#e13) 由维护者操作。

<a id="e13"></a>

## E13：备份、回退、磁盘空间或灾难恢复

| 现象 | 怎么处理 |
| --- | --- |
| 手动备份报目标已存在 | 换新文件名，或等一秒再运行手册命令；备份不会覆盖已有快照 |
| 备份权限失败 | [E02](#e02)，backups 应归配置的 APP_UID/GID |
| 发布失败，需要恢复旧版 | 由维护者按下方说明检出旧提交和恢复两个镜像 digest；没有自动回退脚本 |
| `No space left on device` | 先检查空间并下载备份；清理确认不需要的镜像缓存或旧快照，不清空 data/assets |

检查空间：

```bash
df -h "$HOME/c156"
du -sh "$HOME/c156/data" "$HOME/c156/assets" "$HOME/c156/backups"
```

**正常结果：** 文件系统有可用空间；查清占用后再清理。备份和 Docker 镜像缓存需要按需清理。

**维护者手动恢复旧版本：** 暂停新的发布，记录当前 git rev-parse HEAD 和 images.env。找到最后一次成功 Actions 的提交 SHA 及两个镜像 digest，git checkout --detach 到该 SHA，把对应 BUILD_SHA、BACKEND_IMAGE、FRONTEND_IMAGE、DEPLOY_SEQUENCE 写回 images.env。然后在项目根目录 source deploy/common.sh、load_config "$PWD"，执行 compose_for "$PWD" pull 和 compose_for "$PWD" up -d --wait。若配置有变化，一并恢复项目代理片段、校验重载 Nginx，再检查两个 HTTPS SHA。此操作保留数据库和资产；跨版本数据库不兼容时先人工评估，不能直接恢复旧快照。

**数据库灾难恢复交给维护者处理：** 停止 frontend/backend；将整个 data 目录留作事故副本；将验证过的快照复制为新的 data/c156.sqlite，设置配置的 UID/GID；用对应镜像的 `python -m src.storage init --database /data/c156.sqlite` 验证既有 schema 并配置 WAL；再启动、检查正文。资产从单独备份恢复。init 不做跨版本迁移，不把旧 WAL/SHM 与快照混用，不在服务运行时覆盖数据库。

## 交给维护者的报错信息

提供这四项即可，密码和私钥不要粘贴：

- 手册第几步、排错编号。
- 运行的命令及完整报错。
- Actions 运行链接、失败 job 和步骤名（如果涉及 Actions）。
- E10 中的容器状态与相关日志（如果涉及应用）。
