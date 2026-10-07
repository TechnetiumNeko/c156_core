"""Strict body parsing and existing session proofs shared by adapter routes."""
from fastapi import Request
from .auth import parse_identity, require_session_csrf
from .schemas import validate_body
from .transport import read_json_object, read_query


def body(model):
    async def parse(request: Request):
        read_query(request)
        return validate_body(model, await read_json_object(request))
    return parse


def write_identity(request: Request):
    identity = parse_identity(request)
    require_session_csrf(request, request.app.state.services, identity)
    return identity
