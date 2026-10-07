"""Strict bounded JSON parsing and pure ASGI host/origin protection."""
import json
import math
from .errors import RequestError, error_response

MAX_BODY_BYTES = 2097152


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError('nonfinite number')


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('nonfinite number')
    return number


async def read_json_object(request):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise RequestError(413, 'payload_too_large', 'Request body too large.')
        body.extend(chunk)
    try:
        value = json.loads(body, object_pairs_hook=_pairs, parse_constant=_nonfinite, parse_float=_finite_float)
        if not isinstance(value, dict):
            raise ValueError('object required')
    except (ValueError, UnicodeError, RecursionError):
        raise RequestError(400, 'invalid_request', 'Expected a valid JSON object.') from None
    return value


def read_query(request, *, required=()):
    pairs = request.query_params.multi_items()
    if len(pairs) != len(dict(pairs)) or set(dict(pairs)) != set(required):
        raise RequestError(400, 'invalid_request', 'Missing, unknown or duplicate query fields.')
    return dict(pairs)


class TransportGuard:
    def __init__(self, app, config):
        self.app, self.config = app, config

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = scope.get('headers', [])
        hosts = [v.decode('latin1') for k, v in headers if k.lower() == b'host']
        origins = [v.decode('latin1') for k, v in headers if k.lower() == b'origin']
        if len(hosts) != 1 or hosts[0] not in self.config.allowed_hosts or (origins and (len(origins) != 1 or origins[0] not in self.config.allowed_origins)):
            return await error_response(403, 'forbidden', 'Access denied.')(scope, receive, send)
        async def guarded_send(message):
            if message['type'] == 'http.response.start':
                message['headers'] = [(k, v) for k, v in message.get('headers', []) if k.lower() != b'cache-control'] + [(b'cache-control', b'no-store')]
            await send(message)
        await self.app(scope, receive, guarded_send)
