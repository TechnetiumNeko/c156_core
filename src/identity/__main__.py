"""Local identity management commands; passwords never enter argv."""
import argparse
import getpass
import sys

from ..core.errors import ContentError, InvalidArgument
from ..storage.database import Database
from ..storage.errors import StorageError
from ..services.bootstrap import bootstrap_admin


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m src.identity')
    commands = parser.add_subparsers(dest='command', required=True)
    bootstrap = commands.add_parser('bootstrap-admin')
    bootstrap.add_argument('--database', required=True)
    bootstrap.add_argument('--login-name', required=True)
    bootstrap.add_argument('--display-name', required=True)
    args = parser.parse_args(argv)
    try:
        password = getpass.getpass('Password: ')
        confirmation = getpass.getpass('Confirm password: ')
        if password != confirmation:
            raise InvalidArgument('passwords do not match')
        user = bootstrap_admin(Database(args.database), args.login_name, args.display_name, password)
    except (ContentError, StorageError) as exc:
        print('error: ' + str(exc), file=sys.stderr)
        return 1
    print('Created administrator ' + user.login_name)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
