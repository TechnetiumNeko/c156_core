"""Sanitized HTTP errors; domain details never cross the boundary."""
import logging
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from ..core.errors import ContentError

logger = logging.getLogger(__name__)

class RequestError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def error_response(status, code, message, *, secure=False, retry_after=None):
    response = JSONResponse({'error': {'code': code, 'message': message, 'details': {}}}, status_code=status)
    response.headers['Cache-Control'] = 'no-store'
    if status == 401:
        response.delete_cookie('c156_session', path='/', httponly=True, samesite='strict', secure=secure)
    if retry_after is not None:
        response.headers['Retry-After'] = str(retry_after)
    return response


def install_errors(app):
    async def handle(request, error):
        retry = None
        if isinstance(error, RequestError):
            status, code, message = error.status, error.code, error.message
        elif isinstance(error, (RequestValidationError,)):
            status, code, message = 422, 'invalid_request', 'Invalid request fields.'
        elif isinstance(error, HTTPException):
            status = error.status_code
            code, message = ('method_not_allowed', 'Method not allowed.') if status == 405 else ('not_found', 'Route not found.')
        elif isinstance(error, ContentError):
            code = error.code
            status = {'unauthenticated': 401, 'forbidden': 403, 'frozen': 403, 'not_found': 404,
                      'conflict': 409, 'already_exists': 409, 'rate_limited': 429, 'storage_busy': 503}.get(code, 400)
            message = {401: 'Authentication failed.', 403: 'Access denied.', 404: 'Not found.',
                       409: 'Content changed.', 429: 'Too many requests.', 503: 'Service temporarily busy.'}.get(status, 'Invalid request.')
            if status == 429:
                retry = max(1, int(error.details.get('retry_after', 1)))
        else:
            status, code, message = 500, 'internal_error', 'Internal server error.'
            logger.error('request failed: %s', type(error).__name__)
        return error_response(status, code, message, secure=request.app.state.config.cookie_secure, retry_after=retry)
    for kind in (RequestError, RequestValidationError, HTTPException, ContentError, Exception):
        app.add_exception_handler(kind, handle)
