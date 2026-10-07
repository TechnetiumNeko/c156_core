# TEST 起步清单：我要配什么

你的 test 服务器用 **deploy 用户**，项目目录是 **/home/deploy/c156**，ACR 仓库公开。

测试域名示例为 `c156.secret-sealing.club`；自己的服务器仍以 config.env 实际值为准。已完成的安装不用重新初始化数据库，后续发布按第 3 节末尾说明执行。

## 1. GitHub 配这 8 项，另有 2 项可选

进入 **Settings → Secrets and variables → Actions**。不用创建 Environment，不用填写服务器主机指纹。

### Variables：普通配置

| 名称 | 填什么 | 当前检查 |
| --- | --- | --- |
| `TEST_DEPLOY_ENABLED` | `true` | 已启用 |
| `TEST_ACR_REGISTRY` | ACR 控制台的公网域名，不带 https:// 或仓库路径 | 已存在，格式正确 |
| `TEST_ACR_NAMESPACE` | ACR 命名空间 | 已存在，格式正确 |
| `TEST_DEPLOY_USER` | `deploy` | 已存在，格式正确 |
| `TEST_DEPLOY_PORT`（可选） | 默认 `22`，改过 SSH 端口才必填 | 已存在，格式正确 |
| `TEST_DEPLOY_ROOT`（可选） | 默认自动使用部署用户的家目录/c156；你的为 `/home/deploy/c156` | 已存在，可以保留 |

### Secrets：服务器地址和凭据

| 名称 | 填什么 | 当前检查 |
| --- | --- | --- |
| `TEST_DEPLOY_HOST` | 服务器公网 IP／SSH 域名 | 已存在 |
| `TEST_ACR_USERNAME` | ACR 登录用户名 | 已存在 |
| `TEST_ACR_PASSWORD` | ACR 登录密码／访问凭据，供 Actions 推送镜像 | 已存在 |
| `TEST_DEPLOY_SSH_KEY` | 登录 deploy 账号的专用 SSH 私钥全文 | 已存在 |

不用再添加已取消的主机身份记录参数。公开 ACR 只省掉**服务器拉镜像的登录**，Actions 推镜像仍需要 ACR 用户名和密码。

## 2. GitHub 配齐后，服务器还要准备什么

这部分没有做过才需要做，已准备好的不用重建。

1. 在 ACR 命名空间确认已有两个公开仓库：`c156-backend`、`c156-frontend`。
2. 确认 SSH 私钥对应公钥已放进 **deploy 用户**的 `/home/deploy/.ssh/authorized_keys`；操作见 [SSH 手册](SSH.md)。
3. 准备项目目录和 config.env；操作见下面这一段。
4. 确认 deploy 用户能使用 Docker，并能完成目录权限设置；Nginx 由管理员或面板校验／重载。卡住查 [E02](TROUBLESHOOTING.md#e02)／[E08](TROUBLESHOOTING.md#e08)。

服务器按[部署手册第 1～2 步](README.md)克隆或安全更新源码并运行 setup，避免继续使用旧 clone 的脚本。测试配置示例：

```dotenv
SITE_DOMAIN=c156.secret-sealing.club
DEPLOY_ROOT=
APP_UID=
APP_GID=
NGINX_MANAGED=0
```

setup 会记录实际目录与 deploy 身份。已填旧值 10001 时，在首次 setup 前清空 APP_UID/GID；已有数据权限问题按 [E02](TROUBLESHOOTING.md#e02)处理。不要重复覆盖已有 config.env。

手动 Ubuntu Nginx 按[第 5 步 A](README.md#ubuntu-nginx)配置 sites-available/sites-enabled 和 Certbot；已有面板按第 5 步 B。两者都由管理员或面板重载，deploy 用户无需 Nginx 权限。

## 3. 下一步怎么点

**先确认本次部署代码已经合并进 main。** 仅推送功能分支还不能在 main 上运行新工作流。

1. Actions → **Test, build and deploy** → **Run workflow**。
2. 分支选 **main**，target_environment 选 **test**，**勾选 prepare_only**。
3. 等 checks、images、deploy 都变绿。
4. 在服务器运行 `test -s ~/c156/images.env && echo 'Images ready'`，确认镜像配置已写入。
5. 仅新空库运行初始化、设置管理员密码；配置域名、HTTPS 和代理，按照[手册第 4～5 步](README.md)做，记得替换目录。
6. 再次 Run workflow，仍选 main／test，**不勾选 prepare_only**，正式发布。

首次准备成功只表示文件和镜像到位，尚未启动网站。Secrets 填对与否、Actions 到服务器的连接，以及公开仓库能否匿名拉取，会在这些步骤中实际验证。

报错时从 [排错编号表](TROUBLESHOOTING.md)进入对应项。main 以后会自动更新 test；首次准备期间先完成服务器配置，再安排新的 main 推送。prod 仍是另外一组参数、手动选择发布。

已完成首次安装且数据库、管理员和 HTTPS 正常时，直接正式发布，不重复 prepare 或 `--init-db`。若 main 自动正式发布早于首次准备而因数据库缺失失败，先手动运行 prepare，再初始化，完成 HTTPS 后开新一次正式运行。
