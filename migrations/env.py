"""Generate SQLite migration SQL without taking ownership of a DBAPI connection.

Online upgrades must use `python -m src.storage upgrade` so schema recognition,
foreign-key handling, and transaction ownership cannot be bypassed accidentally.
"""
from alembic import context

if not context.is_offline_mode():
    raise RuntimeError("Use python -m src.storage upgrade --database PATH")

context.configure(
    url="sqlite://",
    literal_binds=True,
    dialect_opts={"paramstyle": "named"},
    transactional_ddl=False,
)
with context.begin_transaction():
    context.run_migrations()
