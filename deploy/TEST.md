# TEST 起步清单：我要配什么

你的 test 服务器用 **deploy 用户**，项目目录是 **/home/deploy/c156**，ACR 仓库公开。

**2026-10-07 检查记录：下面 6 项 Variables、4 项 Secrets 已全部存在。** 首次 main 流水线已通过测试、镜像构建、ACR 推送和 SSH 登录；因服务器尚未运行 setup 而停止。配置名称不因本次简化而变化。

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
4. 确认 deploy 用户能使用 Docker，并能完成目录权限设置和 Nginx 校验／重载。卡住查 [E02](TROUBLESHOOTING.md#e02)／[E08](TROUBLESHOOTING.md#e08)。

维护者确认部署代码已在 main 后，首次在 deploy 用户终端直接运行：

```bash
git clone https://github.com/TechnetiumNeko/c156_core.git "$HOME/c156"
cd "$HOME/c156"
if [ ! -f config.env ]; then
  install -m 600 deploy/config.env.example config.env
fi
```

**你已经 clone 过，不需要重复 clone：** 先确认本次简化 PR 已合并 main，执行下面的一次性更新，再复制配置和运行 setup。尚未运行 setup 的旧 clone 必须先更新，否则用到的仍是旧 UID/GID 规则。

```bash
cd "$HOME/c156"
if git diff --quiet && git diff --cached --quiet; then
  git fetch origin main && git merge --ff-only origin/main
else
  echo '源码有本地修改，先交给维护者处理；不要强制覆盖。'
fi
```

打开项目根目录的 config.env，填写你的测试域名：

```dotenv
SITE_DOMAIN=你的真实测试域名
```

DEPLOY_ROOT 保持留空即可；setup 会自动写入实际目录，**无需手动改路径**。SITE_DOMAIN 只填域名，不带协议或路径。若已复制旧配置，把 APP_UID 和 APP_GID 两项清空，新 setup 会记录 deploy 用户的实际身份。Nginx binary 不在 PATH 时按面板实际路径填 NGINX_BIN。

保存 config.env 后运行 `bash "$HOME/c156/deploy/setup.sh"`，看到 `Directories ready` 再继续。权限错误交给服务器管理员处理，不给 data 目录 chmod 777。

[部署手册](README.md)的终端命令默认使用当前用户的 $HOME/c156；你的 Nginx include 绝对路径为 /home/deploy/c156/nginx/proxy.inc。代码、config.env 和运行目录都在同一个 ~/c156 下；config.env、images.env 及运行状态已加入 Git 忽略。后续 Actions 自动 git fetch 并检出本次提交；不要在服务器修改受 Git 跟踪的源码。

## 3. 下一步怎么点

**先确认本次部署代码已经合并进 main。** 仅推送功能分支还不能在 main 上运行新工作流。

1. Actions → **Test, build and deploy** → **Run workflow**。
2. 分支选 **main**，target_environment 选 **test**，**勾选 prepare_only**。
3. 等 checks、images、deploy 都变绿。
4. 在服务器运行 `test -s ~/c156/images.env && echo 'Images ready'`，确认镜像配置已写入。
5. 仅新空库运行初始化、设置管理员密码；配置面板域名、HTTPS 和代理，按照[手册第 4～5 步](README.md)做，记得替换目录。
6. 再次 Run workflow，仍选 main／test，**不勾选 prepare_only**，正式发布。

首次准备成功只表示文件和镜像到位，尚未启动网站。Secrets 填对与否、Actions 到服务器的连接，以及公开仓库能否匿名拉取，会在这些步骤中实际验证。

报错时从 [排错编号表](TROUBLESHOOTING.md)进入对应项。main 以后会自动更新 test；首次准备期间先完成服务器配置，再安排新的 main 推送。prod 仍是另外一组参数、手动选择发布。
