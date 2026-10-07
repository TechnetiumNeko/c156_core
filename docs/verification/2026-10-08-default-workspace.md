# 默认工作区管理与轻量邀请

用户确认只管理现有默认工作区，多工作区创建及切换暂缓；选择邀请时使用原激活码并预先分配角色，不保存激活凭据原文、不修改数据库协议。

## 实现

- 工作区管理页：默认阅读范围、现有成员角色、移除成员、owner 所有权转交。非 owner 不展示管理员任免入口。
- 轻量邀请：同时校验站点管理员和工作区 admin/owner；owner 可以分配 admin，其他工作区管理员只能分配 reader/editor，不能通过邀请分配 owner。
- WorkspaceInvitationService 将账号、激活凭据哈希、成员关系、授权版本和审计放入同一事务。账号保持 invited，未激活无法登录；激活不改写预先配置的成员角色。
- 站点账号页仍支持只创建账号，文案指向带角色的工作区邀请流程。
- 页面显示待激活、停用、待重置及已移除状态。凭据仅显示在当前页面内存，设置刷新保留页面实例，退出或离开页面清除。

## 验证

- `python -m unittest tests.test_server_management tests.test_accounts tests.test_access_management tests.test_architecture -q`：21 项相关检查通过。
- 实际临时数据库 HTTP 用例验证邀请、未激活不能登录、8 位密码激活、登录后具有 editor 角色并成功创建文档；非法 owner 邀请、过期授权版本、只有站点管理权的邀请被拒绝，未遗留账号。
- HTTP 用例验证阅读范围设置、版本冲突、所有权转交后的原 owner 降为 admin 且不能再次转交。
- `npm --prefix frontend test`、`npm --prefix frontend run typecheck`、`npm --prefix frontend run build`：通过。
- `git diff --check`：通过。
- 独立只读审阅：未发现具体重要问题，确认邀请事务、双重管理授权和请求 epoch 校验。

未自动执行浏览器点击、表单填写或所有权转交交互。未创建真实账号、改动运行库、推送或触发部署；同一事务中意外基础设施失败的注入测试未新增，沿用现有事务实现。编辑器已有约 502 kB 异步 chunk 的构建体积提示保持不变。
