"""Explicit immutable local server configuration."""
from dataclasses import dataclass
from ipaddress import ip_address
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

    def __post_init__(self):
        if self.host != 'localhost':
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
