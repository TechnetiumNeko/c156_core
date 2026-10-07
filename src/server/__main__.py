"""Run the local FastAPI adapter without initializing a library."""
import argparse
import os
from pathlib import Path

from .config import ServerConfig, loopback_hosts, loopback_origins


def main(argv=None):
    parser = argparse.ArgumentParser(description='C156 本地 FastAPI 服务')
    parser.add_argument('--database', type=Path, default=Path(os.environ.get('C156_DATABASE', str(Path(__file__).resolve().parents[2] / 'data' / 'c156.sqlite'))))
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8001)
    parser.add_argument('--allowed-host', action='append')
    parser.add_argument('--allowed-origin', action='append')
    parser.add_argument('--production', action='store_true')
    parser.add_argument('--public-origin', default=os.environ.get('C156_PUBLIC_ORIGIN'))
    parser.add_argument('--trusted-proxy-cidr', action='append')
    args = parser.parse_args(argv)
    try:
        config = ServerConfig(args.database, host=args.host, port=args.port,
            mode='production' if args.production else 'local', public_origin=args.public_origin,
            trusted_proxy_cidrs=tuple(args.trusted_proxy_cidr or filter(None, os.environ.get('C156_PROXY_NETWORK', '').split(','))),
            build_sha=os.environ.get('BUILD_SHA', 'dev'),
            allowed_hosts=tuple(args.allowed_host) if args.allowed_host else loopback_hosts(args.port),
            allowed_origins=tuple(args.allowed_origin) if args.allowed_origin else loopback_origins(args.port))
    except ValueError as error:
        parser.error(str(error))
    import uvicorn
    from .app import create_app
    uvicorn.run(create_app(config), host=config.host, port=config.port, workers=1,
                access_log=False, proxy_headers=config.mode == 'production',
                forwarded_allow_ips=','.join(config.trusted_proxy_cidrs))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
