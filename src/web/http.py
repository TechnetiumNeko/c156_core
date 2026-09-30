"""Loopback HTTP transport with bounded JSON and an exact asset whitelist."""
import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..core.errors import ContentError
from .api import RequestError

MAX_BODY = 2 * 1024 * 1024
STATIC_ROOT = Path(__file__).resolve().parent / 'static'
STATIC = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/index.html': ('index.html', 'text/html; charset=utf-8'),
    '/styles.css': ('styles.css', 'text/css; charset=utf-8'),
    **{f'/{name}.js': (f'{name}.js', 'text/javascript; charset=utf-8')
       for name in ('app', 'client', 'directory', 'editor-state', 'preview')},
    '/vendor/marked.umd.js': ('vendor/marked.umd.js', 'text/javascript; charset=utf-8'),
    '/vendor/purify.min.js': ('vendor/purify.min.js', 'text/javascript; charset=utf-8'),
}
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
DOMAIN_STATUS = {'not_found': 404, 'path_outside_root': 403,
                 'already_exists': 409, 'conflict': 409, 'storage_busy': 503,
                 'unsupported_schema': 500}


def reject_constant(value):
    raise ValueError('Non-finite JSON numbers are not allowed.')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON fields are not allowed.')
        result[key] = value
    return result


class Handler(BaseHTTPRequestHandler):
    # Each response closes the connection: rejected or partially consumed request
    # bodies cannot be interpreted as a subsequent request.
    protocol_version = 'HTTP/1.0'
    server_version = 'C156'
    sys_version = ''

    def log_message(self, format, *args):
        logging.getLogger(__name__).info(format, *args)

    def _reply(self, status, data, content_type='application/json; charset=utf-8'):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def _host(self):
        hosts = self.headers.get_all('Host', [])
        if len(hosts) != 1:
            raise RequestError(403, 'forbidden', 'Invalid request host.')
        host = hosts[0]
        try:
            parsed = urlsplit('http://' + host)
            port = parsed.port
        except ValueError:
            raise RequestError(403, 'forbidden', 'Invalid request host.') from None
        if (parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
                or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment
                or any(char.isspace() for char in host)
                or (port is not None and not 1 <= port <= 65535)):
            raise RequestError(403, 'forbidden', 'Invalid request host.')
        return host

    def _json_body(self, host):
        origins = self.headers.get_all('Origin', [])
        if origins and (len(origins) != 1 or origins[0] != 'http://' + host):
            raise RequestError(403, 'forbidden', 'Request origin does not match host.')
        nonces = self.headers.get_all('X-C156-Nonce', [])
        if len(nonces) != 1 or not hmac.compare_digest(nonces[0].encode('utf-8'), self.server.api.nonce.encode('ascii')):
            raise RequestError(403, 'forbidden', 'Invalid write nonce.')
        types = self.headers.get_all('Content-Type', [])
        if len(types) != 1 or types[0].split(';', 1)[0].strip().lower() != 'application/json':
            raise RequestError(400, 'invalid_request', 'Content-Type must be application/json.')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit() or self.headers.get('Transfer-Encoding') is not None:
            raise RequestError(400, 'invalid_request', 'A valid Content-Length is required.')
        length = int(lengths[0])
        if length > MAX_BODY:
            raise RequestError(413, 'payload_too_large', 'JSON request exceeds 2 MiB.')
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise RequestError(400, 'invalid_request', 'Incomplete JSON request.')
        try:
            return json.loads(raw.decode('utf-8'), parse_constant=reject_constant, object_pairs_hook=unique_object)
        except (UnicodeError, ValueError, RecursionError):
            raise RequestError(400, 'invalid_request', 'Invalid JSON request.') from None

    def _handle(self):
        try:
            host = self._host()
            try:
                url = urlsplit(self.path)
                query_lists = parse_qs(url.query, keep_blank_values=True, encoding='utf-8', errors='strict')
            except (ValueError, UnicodeError):
                raise RequestError(400, 'invalid_request', 'Invalid request URL.') from None
            if url.scheme or url.netloc or url.fragment:
                raise RequestError(400, 'invalid_request', 'Expected a local request path.')
            if any(len(values) != 1 for values in query_lists.values()):
                raise RequestError(400, 'invalid_request', 'Duplicate query fields.')
            query = {key: values[0] for key, values in query_lists.items()}
            body = self._json_body(host) if self.command in ('POST', 'PUT') else None
            if url.path.startswith('/api/'):
                status, result = self.server.api.dispatch(self.command, url.path, query, body)
                self._reply(status, result)
            elif url.path in STATIC and self.command in ('GET', 'HEAD'):
                if query:
                    raise RequestError(400, 'invalid_request', 'Static routes do not accept query fields.')
                name, content_type = STATIC[url.path]
                try:
                    data = (STATIC_ROOT / name).read_bytes()
                except FileNotFoundError:
                    raise RequestError(404, 'not_found', 'Asset not found.') from None
                self._reply(200, data, content_type)
            else:
                raise RequestError(404, 'not_found', 'Route not found.')
        except RequestError as error:
            self._reply(error.status, {'error': {'code': error.code, 'message': error.message, 'details': {}}})
        except ContentError as error:
            # Busy errors can contain storage diagnostic details; never send those.
            details = {} if error.code in ('storage_busy', 'unsupported_schema') else error.details
            message = 'Content storage is temporarily unavailable.' if error.code in ('storage_busy', 'unsupported_schema') else error.message
            self._reply(DOMAIN_STATUS.get(error.code, 422), {'error': {'code': error.code, 'message': message, 'details': details}})
        except Exception:
            logging.getLogger(__name__).exception('HTTP request failed')
            self._reply(500, {'error': {'code': 'internal_error', 'message': 'Internal server error.', 'details': {}}})

    do_GET = do_POST = do_PUT = do_HEAD = do_DELETE = do_PATCH = do_OPTIONS = _handle
