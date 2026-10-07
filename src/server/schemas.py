"""Only explicitly declared strictly typed fields enter application services."""
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError
from .errors import RequestError

class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class LoginBody(StrictBody):
    login_name: StrictStr
    password: StrictStr

class EmptyBody(StrictBody):
    pass

class SaveDocumentBody(StrictBody):
    object_id: StrictStr
    content: StrictStr
    expected_revision_id: StrictStr


def validate_body(model, value):
    try:
        return model.model_validate(value)
    except ValidationError:
        raise RequestError(422, 'invalid_request', 'Invalid request fields.') from None

from pydantic import StrictInt, StrictBool

class TokenPasswordBody(StrictBody):
    token: StrictStr
    password: StrictStr

class ProfileBody(StrictBody):
    display_name: StrictStr
    expected_version: StrictInt

class PasswordBody(StrictBody):
    old_password: StrictStr
    new_password: StrictStr

class CreateUserBody(StrictBody):
    login_name: StrictStr
    display_name: StrictStr

class UserTargetBody(StrictBody):
    user_id: StrictStr
    expected_version: StrictInt

class SiteAdminBody(UserTargetBody):
    enabled: StrictBool

class AddMemberBody(StrictBody):
    login_name: StrictStr
    role: StrictStr
    expected_version: StrictInt

class MemberRoleBody(UserTargetBody):
    role: StrictStr

class CreateFolderBody(StrictBody):
    parent_id: StrictStr
    name: StrictStr
    visibility: StrictStr = 'inherit'

class CreateDocumentBody(CreateFolderBody):
    content: StrictStr = ''

class RenameBody(StrictBody):
    object_id: StrictStr
    name: StrictStr
    expected_version: StrictInt

class DeleteBody(StrictBody):
    object_id: StrictStr
    expected_version: StrictInt
    recursive: StrictBool = False
    expected_subtree_token: StrictStr | None = None

class ReadScopeBody(StrictBody):
    read_scope: StrictStr
    expected_version: StrictInt

class OwnershipBody(StrictBody):
    target_user_id: StrictStr
    expected_version: StrictInt

class InviteMemberBody(CreateUserBody):
    role: StrictStr
    expected_version: StrictInt
