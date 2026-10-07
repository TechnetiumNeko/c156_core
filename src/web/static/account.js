// Native forms shared by the two management panels. Values are never HTML.
export const el = (tag, text = '') => { const node = document.createElement(tag); node.textContent = text; return node; };
export function button(parent, text, action) { const b = el('button', text); b.type = 'button'; b.onclick = action; parent.append(b); return b; }
export function form(parent, title, fields, action) {
  const f = el('form'); f.append(el('h3', title)); const inputs = {};
  for (const [name, label, options] of fields) {
    const wrapper = el('label', label); let input;
    if (Array.isArray(options)) { input = el('select'); for (const value of options) { const option = el('option', value); option.value = value; input.append(option); } }
    else { input = el('input'); input.type = options || 'text'; input.autocomplete = input.type === 'password' ? 'off' : 'off'; input.required = true; }
    input.name = name; inputs[name] = input; wrapper.append(input); f.append(wrapper);
  }
  const submit = el('button', '提交'); submit.type = 'submit'; f.append(submit);
  f.onsubmit = async event => { event.preventDefault(); submit.disabled = true; try { await action(Object.fromEntries(Object.entries(inputs).map(([k,v])=>[k,v.value]))); } finally { for (const input of Object.values(inputs)) if (input.type === 'password' || input.name === 'token') input.value = ''; submit.disabled = false; } };
  parent.append(f); return inputs;
}
export class AccountPanel {
  constructor({client, panel, run, onLogin, onLogout, onPassword, onProfile, getUser}) { Object.assign(this,{client,panel,run,onLogin,onLogout,onPassword,onProfile,getUser}); this.users = []; }
  clear() { this.users = []; this.panel.replaceChildren(); }
  render(user, initialized = true) {
    this.clear(); const p = this.panel;
    if (!user) {
      p.append(el('p', initialized ? '登录或使用管理员人工转交的凭据激活／重置账号。' : '尚未初始化账号。请在本机使用 bootstrap-admin 显式引导管理员。'));
      form(p,'登录',[['login_name','登录名'],['password','密码','password']],v=>this.run(()=>this.onLogin(v)));
      for (const [kind,title] of [['activate','激活'],['reset','重置密码']]) form(p,title,[['token','一次性凭据'],['password','新密码（8–128 字符）','password']],v=>this.run(async()=>{await this.client[kind](v.token,v.password); this.render(null); p.prepend(el('p','密码已设置，请登录。'));}));
      return;
    }
    p.append(el('p',`${user.display_name} (${user.login_name}) · ${user.status}`));
    button(p,'退出',()=>this.run(this.onLogout));
    form(p,'显示名',[['display_name','显示名']],v=>this.run(async()=>this.onProfile((await this.client.profile(v.display_name,this.getUser().version)).user),async()=>this.onProfile((await this.client.session()).user)));
    form(p,'修改密码',[['old_password','原密码','password'],['new_password','新密码（8–128 字符）','password']],v=>this.run(()=>this.onPassword(v)));
    if (user.site_admin) {
      button(p,'刷新账号列表',()=>this.load());
      form(p,'创建账号',[['login_name','登录名'],['display_name','显示名']],v=>this.run(async()=>{this.grant(await this.client.createUser(v.login_name,v.display_name)); await this.load();}));
      this.userList = el('div'); p.append(this.userList); this.credential = el('section'); p.append(this.credential); this.load();
    }
  }
  grant(data) {
    if (!data.token) return;
    this.credential.replaceChildren(el('p',`仅当前页面供人工转交：${data.purpose}，到期 ${data.expires_at}`),el('pre',data.token));
    button(this.credential,'清除凭据',()=>this.credential.replaceChildren());
  }
  async load() { await this.run(async()=>{const {users} = await this.client.users(); this.users = users; this.userList.replaceChildren(); for (const user of users) {
    const row=el('section'); row.append(el('p',`${user.display_name} · ${user.login_name} · ${user.status} · v${user.version} · ${user.site_admin?'站点管理员':'普通账号'}`));
    for (const [action,label] of [['activation','重发激活'],['reset','发起重置'],['disable','停用'],['enable','启用']]) button(row,label,()=>this.run(async()=>{const result=await this.client.userAction(action,user.id,user.version); this.grant(result); await this.load();},()=>this.load()));
    button(row,user.site_admin?'撤销站点管理':'设为站点管理',()=>this.run(async()=>{const result=await this.client.siteAdmin(user.id,!user.site_admin,user.version); if(this.getUser()?.id===result.user.id)this.onProfile(result.user);else await this.load();},()=>this.load()));
    this.userList.append(row);
  }}); }
}
