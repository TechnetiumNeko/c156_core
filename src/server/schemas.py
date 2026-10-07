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
