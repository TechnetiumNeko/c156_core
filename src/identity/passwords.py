"""The sole Argon2 dependency adapter; no weaker fallback is available."""
try:
    from argon2 import PasswordHasher as Argon2Hasher, Type
    from argon2.exceptions import VerifyMismatchError
except ImportError as exc:
    raise RuntimeError('identity requires argon2-cffi==25.1.0; install with python -m pip install -r requirements.txt') from exc


class PasswordHasher:
    def __init__(self) -> None:
        self._hasher = Argon2Hasher(time_cost=2, memory_cost=19456, parallelism=1,
                                    salt_len=16, hash_len=32, type=Type.ID)
        self.dummy_hash = self.hash('fixed dummy password for unknown accounts')

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, encoded: str, password: str) -> bool:
        try:
            return self._hasher.verify(encoded, password)
        except VerifyMismatchError:
            return False
