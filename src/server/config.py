"""Explicit immutable local server configuration."""
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit
from pathlib import Path


def loopback_hosts(port):
    return (f'127.0.0.1:{port}', f'localhost:{port}', '127.0.0.1:5173', 'localhost:5173')


def loopback_origins(port):
    return tuple('http://' + host for host in loopback_hosts(port))


@dataclass(frozen=True)
class ServerConfig:
    database_path: Path
    host: str = '127.0.0.1'
    port: int = 8001
    allowed_hosts: tuple[str, ...] = loopback_hosts(8001)
    allowed_origins: tuple[str, ...] = loopback_origins(8001)
    cookie_secure: bool = False

    mode: str = 'local'
    public_origin: str | None = None
    trusted_proxy_cidrs: tuple[str, ...] = ()
    build_sha: str = 'dev'

    def __post_init__(self):
        if self.mode not in ('local', 'production'):
            raise ValueError('mode must be local or production')
        if self.mode == 'production':
            origin = urlsplit(self.public_origin or '')
            if (origin.scheme != 'https' or not origin.hostname or origin.username
                    or origin.password or origin.path or origin.query or origin.fragment
                    or origin.netloc != origin.netloc.lower()):
                raise ValueError('production requires a canonical HTTPS public origin')
            origin.port  # Reject malformed ports.
            if not self.trusted_proxy_cidrs or isinstance(self.trusted_proxy_cidrs, str):
                raise ValueError('production requires explicit trusted proxy networks')
            networks = tuple(ip_network(value, strict=True) for value in self.trusted_proxy_cidrs)
            if any(not network.is_private or network.prefixlen == 0 for network in networks):
                raise ValueError('trusted proxies must be limited private networks')
            object.__setattr__(self, 'trusted_proxy_cidrs', tuple(str(n) for n in networks))
            object.__setattr__(self, 'allowed_hosts', (origin.netloc,))
            object.__setattr__(self, 'allowed_origins', (self.public_origin,))
            object.__setattr__(self, 'cookie_secure', True)
        elif self.trusted_proxy_cidrs or self.public_origin:
            raise ValueError('proxy settings require production mode')
        if not isinstance(self.build_sha, str) or not self.build_sha or len(self.build_sha) > 64:
            raise ValueError('build_sha must contain 1 to 64 characters')
        if self.mode == 'local' and self.host != 'localhost':
            try:
                local = ip_address(self.host).is_loopback
            except ValueError:
                local = False
            if not local:
                raise ValueError('host must be a loopback address')
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('port must be an integer from 1 to 65535')
        if type(self.cookie_secure) is not bool:
            raise ValueError('cookie_secure must be boolean')
        object.__setattr__(self, 'database_path', Path(self.database_path))
        for name in ('allowed_hosts', 'allowed_origins'):
            values = getattr(self, name)
            if not values or isinstance(values, str) or any(type(value) is not str or not value for value in values):
                raise ValueError(f'{name} must contain nonempty strings')
            object.__setattr__(self, name, tuple(values))
