"""Atomic account invitation and preconfigured workspace membership."""
from dataclasses import dataclass
from .accounts import AccountService
from .access import AccessService
from .unit_of_work import ApplicationUnitOfWork
from ..identity.models import AccountTokenGrant
from ..access.models import WorkspaceAccessView
from ..storage.access_repository import MembershipRecord


@dataclass(frozen=True)
class WorkspaceInvitation:
    grant: AccountTokenGrant
    workspace: WorkspaceAccessView


class WorkspaceInvitationService:
    def __init__(self, database, *, clock=None):
        self._uow = ApplicationUnitOfWork(database, clock=clock)

    def invite(self, scope, login_name, display_name, role, *, session_token, expected_version):
        with self._uow.transaction(write=True) as work:
            account_actor = AccountService._admin(work, session_token)
            actor, manager, repo, settings = AccessService._manager(work, scope, session_token, expected_version)
            AccessService._role(manager, None, role)
            before = AccessService._state(repo)
            grant = AccountService._create_invited_user(work, login_name, display_name, account_actor)
            now = work.now.isoformat()
            # Membership is configured now. Identity rejects invited accounts, so
            # no principal can use it until the original activation code is consumed.
            repo.insert_membership(MembershipRecord(scope.workspace_id, grant.user.id, role, 'active', 1, now, now))
            AccessService._finish(work, scope, actor, repo, settings.version, before,
                                 'invite_member', 'membership', grant.user.id)
            return WorkspaceInvitation(grant, AccessService._workspace(work, repo))
