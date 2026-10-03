"""Workspace authorization management within one identity and write snapshot."""
import json
from dataclasses import asdict, replace
from uuid import uuid4

from ..access.models import AccessRule, MembershipView, WorkspaceAccessView, ObjectAccessView
from ..access.policy import AccessPolicy, ACTIONS
from ..core.errors import Conflict, Forbidden, InvalidArgument, NotFound, Unauthenticated
from ..identity.models import user_view
from ..identity.validation import normalize_login_name
from ..storage.access_repository import MembershipRecord, AccessRuleRecord
from ..storage.audit_repository import AuditEventRecord
from .unit_of_work import ApplicationUnitOfWork


_READ_ONLY = object()


class AccessService:
    def __init__(self, database, *, clock=None):
        self._uow = ApplicationUnitOfWork(database, clock=clock)

    @staticmethod
    def _manager(work, scope, token, expected_version=_READ_ONLY):
        actor = work.resolve_principal(token)
        if actor.user_id is None:
            raise Unauthenticated('authentication required')
        content = work.content(scope)
        content.get_entry(scope, scope.root_id)
        repo = work.access(scope)
        member = repo.get_membership(actor.user_id) if actor.user_id else None
        if member is None or member.status != 'active' or member.role not in ('admin', 'owner'):
            raise Forbidden('workspace administrator required')
        settings = repo.get_settings()
        if settings is None:
            raise NotFound('workspace settings not found')
        if expected_version is not _READ_ONLY and (type(expected_version) is not int or settings.version != expected_version):
            raise Conflict('workspace authorization changed')
        return actor, member, repo, settings

    @staticmethod
    def _workspace(work, repo):
        settings = repo.get_settings()
        members = tuple(MembershipView(user_view(work.identity.get_user(m.user_id)), m.role, m.status)
                        for m in repo.list_memberships())
        return WorkspaceAccessView(settings.version, settings.read_scope, members)

    @staticmethod
    def _object(work, scope, actor, member, repo, object_id):
        content = work.content(scope)
        content.get_entry(scope, object_id)
        chain = content.ancestor_chain(object_id)
        settings = repo.get_settings()
        rules = repo.list_rules()
        privacy = {r.object_id: r.owner_id for r in repo.list_privacy()}
        locks = {r.object_id: r.locked_by for r in repo.list_locks()}
        policy = AccessPolicy(actor, member, settings, rules=rules, privacy=privacy, locks=locks, ownership={})
        convert = lambda r: AccessRule(r.object_id, r.subject_type, r.subject_key, r.action, r.effect)
        ancestors = {node.object_id for node in chain[:-1]}
        return ObjectAccessView(settings.version, tuple(convert(r) for r in rules if r.object_id == object_id),
            'private' if object_id in privacy else 'inherit', privacy.get(object_id), locks.get(object_id),
            tuple(convert(r) for r in rules if r.object_id in ancestors),
            {action: policy.decide(chain, action) for action in ACTIONS})

    @staticmethod
    def _state(repo):
        # Safe management state only: no credential or content data.
        return {'settings': asdict(repo.get_settings()),
                'members': [asdict(m) for m in repo.list_memberships()],
                'rules': [asdict(r) for r in repo.list_workspace_rules()]}

    @staticmethod
    def _finish(work, scope, actor, repo, version, before, event, target_type, target_id):
        after = AccessService._state(repo)
        if before == after:
            return
        if not repo.update_settings(expected_version=version):
            raise Conflict('workspace authorization changed')
        after = AccessService._state(repo)
        audit_before = {'settings': before['settings']}
        audit_after = {'settings': after['settings']}
        for key in ('members', 'rules'):
            audit_before[key] = [record for record in before[key] if record not in after[key]]
            audit_after[key] = [record for record in after[key] if record not in before[key]]
        work.audit.append(AuditEventRecord(str(uuid4()), actor.user_id, scope.workspace_id,
            'access.' + event, target_type, target_id, json.dumps(audit_before, sort_keys=True),
            json.dumps(audit_after, sort_keys=True), work.now.isoformat()))

    def workspace_access(self, scope, *, session_token):
        with self._uow.transaction() as work:
            _, _, repo, _ = self._manager(work, scope, session_token)
            return self._workspace(work, repo)

    def object_access(self, scope, object_id, *, session_token):
        with self._uow.transaction() as work:
            actor, member, repo, _ = self._manager(work, scope, session_token)
            return self._object(work, scope, actor, member, repo, object_id)

    @staticmethod
    def _role(actor_member, old, role):
        if role not in ('reader', 'editor', 'admin'):
            raise InvalidArgument('ordinary membership cannot create an owner')
        if actor_member.role != 'owner' and (role == 'admin' or old and old.role in ('admin', 'owner')):
            raise Forbidden('owner required to manage administrators')

    def _member_change(self, scope, target, role, *, session_token, expected_version, operation):
        with self._uow.transaction(write=True) as work:
            actor, manager, repo, settings = self._manager(work, scope, session_token, expected_version)
            before = self._state(repo)
            if operation == 'add_member':
                user = work.identity.get_user_by_login_name(normalize_login_name(target))
            else:
                user = work.identity.get_user(target)
            if user is None:
                raise NotFound('account not found')
            old = repo.get_membership(user.id)
            if operation == 'add_member':
                if user.status != 'active':
                    raise InvalidArgument('active account required')
                self._role(manager, old if old and old.status == 'active' else None, role)
                if old and old.status == 'active':
                    if old.role != role:
                        raise Conflict('account is already a member')
                elif old:
                    repo.update_membership(replace(old, role=role, status='active', version=old.version+1,
                        modified_at=work.now.isoformat()), expected_version=old.version)
                else:
                    now = work.now.isoformat()
                    repo.insert_membership(MembershipRecord(scope.workspace_id, user.id, role, 'active', 1, now, now))
            else:
                if old is None or (old.status != 'active' and operation != 'remove_member'):
                    raise NotFound('active membership not found')
                if operation == 'transfer_ownership':
                    if manager.role != 'owner':
                        raise Forbidden('owner required')
                    if user.status != 'active' or user.id == actor.user_id:
                        raise InvalidArgument('another active target member required')
                    if user.id != actor.user_id:
                        repo.update_membership(replace(manager, role='admin', version=manager.version+1,
                            modified_at=work.now.isoformat()), expected_version=manager.version)
                        repo.update_membership(replace(old, role='owner', version=old.version+1,
                            modified_at=work.now.isoformat()), expected_version=old.version)
                else:
                    new_role = role if operation == 'set_member_role' else old.role
                    self._role(manager, old, role if operation == 'set_member_role' else 'reader')
                    if old.status == 'active' and old.role == 'owner' and repo.count_effective_owners(excluding_user_id=user.id) == 0:
                        raise Forbidden('last effective workspace owner must be retained')
                    status = 'removed' if operation == 'remove_member' else 'active'
                    if (new_role, status) != (old.role, old.status):
                        repo.update_membership(replace(old, role=new_role, status=status, version=old.version+1,
                            modified_at=work.now.isoformat()), expected_version=old.version)
                    if operation == 'remove_member':
                        repo.delete_user_rules(user.id)
            self._finish(work, scope, actor, repo, settings.version, before, operation, 'membership', user.id)
            return self._workspace(work, repo)

    def add_member(self, scope, login_name, role, *, session_token, expected_version):
        return self._member_change(scope, login_name, role, session_token=session_token, expected_version=expected_version, operation='add_member')

    def set_member_role(self, scope, user_id, role, *, session_token, expected_version):
        return self._member_change(scope, user_id, role, session_token=session_token, expected_version=expected_version, operation='set_member_role')

    def remove_member(self, scope, user_id, *, session_token, expected_version):
        return self._member_change(scope, user_id, None, session_token=session_token, expected_version=expected_version, operation='remove_member')

    def transfer_ownership(self, scope, target_user_id, *, session_token, expected_version):
        return self._member_change(scope, target_user_id, None, session_token=session_token, expected_version=expected_version, operation='transfer_ownership')

    def set_read_scope(self, scope, read_scope, *, session_token, expected_version):
        with self._uow.transaction(write=True) as work:
            actor, _, repo, settings = self._manager(work, scope, session_token, expected_version)
            if read_scope not in ('members', 'authenticated', 'everyone'):
                raise InvalidArgument('invalid read scope')
            if settings.read_scope != read_scope:
                before = self._state(repo)
                repo.set_read_scope(read_scope)
                self._finish(work, scope, actor, repo, settings.version, before, 'set_read_scope', 'workspace', scope.workspace_id)
            return self._workspace(work, repo)

    def _rule_change(self, scope, rule, *, session_token, expected_version, remove):
        with self._uow.transaction(write=True) as work:
            actor, member, repo, settings = self._manager(work, scope, session_token, expected_version)
            entry = work.content(scope).get_entry(scope, rule.object_id)
            if rule.action not in ACTIONS or (not remove and rule.effect not in ('allow', 'deny')):
                raise InvalidArgument('invalid rule action or effect')
            if rule.action == 'create' and entry.kind != 'folder':
                raise InvalidArgument('create rules require a folder')
            if rule.subject_type == 'role':
                valid = rule.subject_key in ('reader', 'editor', 'admin', 'owner')
            elif rule.subject_type in ('authenticated', 'everyone'):
                valid = rule.subject_key == '' and rule.action == 'read'
            elif rule.subject_type == 'user':
                user = work.identity.get_user(rule.subject_key)
                target = repo.get_membership(rule.subject_key)
                valid = user is not None and user.status == 'active' and target is not None and target.status == 'active'
            else:
                valid = False
            if not valid:
                raise InvalidArgument('invalid rule subject')
            record = AccessRuleRecord(scope.workspace_id, scope.branch_id, rule.object_id,
                rule.subject_type, rule.subject_key, rule.subject_key if rule.subject_type == 'user' else None,
                rule.action, rule.effect)
            before = self._state(repo)
            if remove:
                repo.delete_rule(record)
            else:
                repo.upsert_rule(record)
            self._finish(work, scope, actor, repo, settings.version, before,
                'remove_rule' if remove else 'put_rule', 'object', rule.object_id)
            return self._object(work, scope, actor, member, repo, rule.object_id)

    def put_rule(self, scope, rule, *, session_token, expected_version):
        return self._rule_change(scope, rule, session_token=session_token, expected_version=expected_version, remove=False)

    def remove_rule(self, scope, rule, *, session_token, expected_version):
        return self._rule_change(scope, rule, session_token=session_token, expected_version=expected_version, remove=True)
