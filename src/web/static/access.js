import {el,button,form} from './account.js';
export class AccessPanel {
  constructor({client,panel,run,changed,getRole,getObject}) { Object.assign(this,{client,panel,run,changed,getRole,getObject}); this.workspace=null; this.object=null; }
  clear() { this.workspace=null; this.object=null; this.panel.replaceChildren(); }
  async load() { this.clear(); if (!['admin','owner'].includes(this.getRole())) return;
    await this.run(async()=>{this.workspace=(await this.client.members()).workspace; const id=this.getObject(); if(id) this.object=(await this.client.access(id)).access; this.render(id);});
  }
  async mutate(action) { const epoch=this.client.epoch; await this.run(async()=>{await action(); await this.load(); if(epoch!==this.client.epoch)return; await this.changed();},()=>this.load()); }
  render(id) {
    const p=this.panel,w=this.workspace,owner=this.getRole()==='owner';
    p.append(el('p',`工作区授权版本 ${w.version}`));
    form(p,'阅读范围',[['read_scope','范围',['members','authenticated','everyone']]],v=>this.mutate(()=>this.client.readScope(v.read_scope,w.version))).read_scope.value=w.read_scope;
    const roles=owner?['reader','editor','admin']:['reader','editor'];
    form(p,'精确添加成员',[['login_name','完整登录名'],['role','角色',roles]],v=>this.mutate(()=>this.client.addMember(v.login_name,v.role,w.version)));
    for(const m of w.members) {
      const row=el('section'); row.append(el('p',`${m.user.login_name} · ${m.role} · ${m.status}`));
      if(m.role!=='owner' && (owner || m.role!=='admin')) { form(row,'更改角色',[['role','角色',roles]],v=>this.mutate(()=>this.client.changeMember(m.user.id,v.role,w.version))); button(row,'移除',()=>this.mutate(()=>this.client.removeMember(m.user.id,w.version))); }
      if(owner && m.role!=='owner') button(row,'转交所有权',()=>{if(window.confirm('将工作区所有权转交给 '+m.user.login_name+'？')) this.mutate(()=>this.client.ownership(m.user.id,w.version));}); p.append(row);
    }
    if(!this.object) return; const a=this.object;
    p.append(el('h3','对象权限'),el('p',`对象 ${id} · 授权版本 ${a.version} · 私密所有者 ${a.private_owner_id || '无'} · 冻结者 ${a.locked_by || '无'}`));
    p.append(el('p','恢复继承可能扩大对象及其后代的可见范围；祖先权限仍然生效。'));
    form(p,'可见范围',[['visibility','范围',['inherit','private']]],v=>{if(window.confirm('可见范围改变可能扩大阅读人群，确认提交？')) return this.mutate(()=>this.client.visibility(id,v.visibility,a.version));}).visibility.value=a.visibility;
    const own=el('section'); own.append(el('h3','自身规则（删除后恢复继承）')); for(const rule of a.rules) { const row=el('p',`${rule.subject_type}:${rule.subject_key} · ${rule.action}=${rule.effect}`); button(row,'删除',()=>this.mutate(()=>this.client.deleteRule({...rule,expected_version:a.version}))); own.append(row); } p.append(own);
    const inherited=el('section'); inherited.append(el('h3','继承规则')); for(const r of a.inherited_rules) inherited.append(el('p',`来源 ${r.object_id} · ${r.subject_type}:${r.subject_key} · ${r.action}=${r.effect}`)); p.append(inherited);
    p.append(el('pre',Object.entries(a.decisions).map(([k,v])=>`${k}: ${v.allowed?'允许':'拒绝'} (${v.reason}, 来源 ${v.source_object_id || '基线'})`).join('\n')));
    const fields=form(p,'添加／覆盖自身规则',[['subject_type','主体',['role','user','authenticated','everyone']],['subject_key','角色名／用户 ID（通用主体留空）'],['action','动作',['read','create','edit','rename','move','delete','review','publish']],['effect','结果',['allow','deny']]],v=>this.mutate(()=>this.client.putRule({object_id:id,...v,expected_version:a.version})));
    fields.subject_key.required=false;
  }
}
