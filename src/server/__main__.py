"""Run the local FastAPI adapter without initializing a library."""
import argparse
from pathlib import Path

from .config import ServerConfig, loopback_hosts, loopback_origins


def main(argv=None):
    parser = argparse.ArgumentParser(description='C156 本地 FastAPI 服务')
    parser.add_argument('--database', type=Path, default=Path(__file__).resolve().parents[2] / 'data' / 'c156.sqlite')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8001)
    parser.add_argument('--allowed-host', action='append')
    parser.add_argument('--allowed-origin', action='append')
    args = parser.parse_args(argv)
    try:
        config = ServerConfig(args.database, host=args.host, port=args.port,
            allowed_hosts=tuple(args.allowed_host) if args.allowed_host else loopback_hosts(args.port),
            allowed_origins=tuple(args.allowed_origin) if args.allowed_origin else loopback_origins(args.port))
    except ValueError as error:
        parser.error(str(error))
    import uvicorn
    from .app import create_app
    uvicorn.run(create_app(config), host=config.host, port=config.port, workers=1,
                access_log=False, proxy_headers=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
