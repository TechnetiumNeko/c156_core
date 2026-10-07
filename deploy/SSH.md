# SSH 部署钥匙：查看、创建和填写 Secret

本页以你的 **test 服务器、deploy 用户**为例。prod 在朋友服务器单独生成一把，将文中的 `test` 文件名和 `TEST_` Secret 名改成 `prod`、`PROD_`。

## 先分清公钥和私钥

| 内容 | 放哪里 | 用途 |
| --- | --- | --- |
| 部署私钥 `c156_test_actions` | GitHub 的 `TEST_DEPLOY_SSH_KEY` Secret | Actions 持有它，登录服务器的 deploy 账号 |
| 对应公钥 `c156_test_actions.pub` | 服务器 `/home/deploy/.ssh/authorized_keys` | 允许这个私钥登录 deploy 账号 |

私钥相当于这次自动部署的登录凭据，公钥相当于服务器的允许名单。公钥文件通常以 `.pub` 结尾，不能把公钥当作私钥填进 Secret。[OpenSSH 说明](https://man.openbsd.org/ssh-keygen)

这与 TouhouCCB 的 `github_deploy` 钥匙是同一种用法。BumperWar 还配了一把“服务器 → GitHub”的 Deploy Key，用来拉仓库；本项目由 Actions 传发布文件和源码归档，因此只需准备“Actions → 服务器”这把。

## 1. 登录正确的服务器账号，查看现有文件

在你的可信服务器终端运行：

```bash
whoami
printf '%s\n' "$HOME"
ls -la "$HOME/.ssh"
```

**正常结果：** 用户为 `deploy`，家目录为 `/home/deploy`。第一次没有 `.ssh` 目录也正常。若显示 root，先用已有管理员权限切换到 deploy，例如 `sudo -iu deploy`，再检查。

不要覆盖已经存在的 `id_ed25519`、`id_rsa` 或 `github_deploy`。它们可能属于其他项目，也可能是服务器连接 GitHub 使用的钥匙。仅有 authorized_keys 或 `.pub` 文件时，不能从它们还原私钥。

已存在某个明确属于本项目的公钥时，可以查看其指纹，例如：

```bash
ssh-keygen -lf "$HOME/.ssh/c156_test_actions.pub"
```

**正常结果：** 一行包含 `SHA256:…` 和 `ED25519`。这只显示公钥指纹，不显示私钥。如果尚未创建，继续第 2 步。

## 2. 创建本项目专用钥匙

仍在 deploy 用户终端，逐段复制运行：

```bash
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
if [ -e "$HOME/.ssh/c156_test_actions" ] || [ -e "$HOME/.ssh/c156_test_actions.pub" ]; then
  printf '已有 c156_test_actions 文件；保留现有钥匙，先确认用途。\n'
else
  ssh-keygen -t ed25519 -C 'c156-test-github-actions' -f "$HOME/.ssh/c156_test_actions" -N ''
fi
```

**正常结果：** 新建后出现两个文件：

```text
/home/deploy/.ssh/c156_test_actions       私钥
/home/deploy/.ssh/c156_test_actions.pub   公钥
```

`-N ''` 表示不设私钥口令，供当前非交互 Actions 流水线使用。只对这把自动部署专用钥匙这样做；不要修改已有个人钥匙的口令。已有文件时不重新生成，确认两份属于本项目且当前流水线可以免口令使用后再继续。

## 3. 把公钥加入 deploy 的允许名单

```bash
touch "$HOME/.ssh/authorized_keys"
C156_PUBLIC_KEY=$(cat "$HOME/.ssh/c156_test_actions.pub")
if ! grep -qxF "$C156_PUBLIC_KEY" "$HOME/.ssh/authorized_keys"; then
  printf '\n%s\n' "$C156_PUBLIC_KEY" >> "$HOME/.ssh/authorized_keys"
fi
chmod 600 "$HOME/.ssh/authorized_keys" "$HOME/.ssh/c156_test_actions"
```

此命令追加新公钥，保留原有登录钥匙；重复执行不会重复追加同一行。也不会修改其他用户的 authorized_keys。

**正常结果：** 命令无报错结束。SSH 不需要因为追加 authorized_keys 而重启。权限或登录报错见 [E05](TROUBLESHOOTING.md#e05)。

## 4. 将私钥填入 GitHub Secret

**只在你自己的可信终端查看，不发到聊天、不截图公开：**

```bash
cat "$HOME/.ssh/c156_test_actions"
```

把完整输出复制到 GitHub：

**仓库 → Settings → Secrets and variables → Actions → Secrets → New repository secret**

```text
Name: TEST_DEPLOY_SSH_KEY
Value: 私钥完整内容，保留所有换行和 BEGIN／END 两行
```

内容应从 `-----BEGIN OPENSSH PRIVATE KEY-----` 开始，以 `-----END OPENSSH PRIVATE KEY-----` 结束。不要填 `.pub` 文件、文件路径或服务器登录密码。

**正常结果：** Secrets 列表出现 TEST_DEPLOY_SSH_KEY。保存后 GitHub 不会再显示其值；需要修改时重新填写。

## 5. 验证这把钥匙能登录

在服务器终端验证。将地址、端口替换为 GitHub 中 TEST_DEPLOY_HOST、TEST_DEPLOY_PORT 的实际值：

```bash
ssh -i "$HOME/.ssh/c156_test_actions" -p 22 \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  deploy@YOUR_SERVER_IP 'whoami'
```

**正常结果：** 输出 `deploy`，不会询问密码或主机是否可信。

| 报错 | 怎么处理 |
| --- | --- |
| Permission denied (publickey) | 确认公钥追加到 deploy 的 authorized_keys，用户和文件所有者正确，权限符合第 3 步 |
| 询问私钥口令／无法解密私钥 | 当前流水线不处理私钥口令；确认用了第 2 步新建的自动部署专用钥匙 |
| Connection timed out／refused | 查 SSH 端口、安全组和防火墙；服务器经公网地址自连也可能被网络规则拦截 |

以上只验证本次连接。GitHub runner 到服务器的实际连通性仍要通过 prepare 工作流确认。登录成功也不自动赋予 Docker、目录 chown 或 Nginx 重载权限，相关问题按 [E02](TROUBLESHOOTING.md#e02)／[E08](TROUBLESHOOTING.md#e08) 处理。

私钥可按现有凭据保管方式安全留存，用于后续更新 Secret。撤销这把钥匙时，从 authorized_keys 删除它对应的那一行公钥，不清空整个文件。prod 用自己的专用钥匙。
