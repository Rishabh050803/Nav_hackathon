"""Store versioned, individually salted scrypt password hashes."""

import hashlib
from hmac import compare_digest
from secrets import token_bytes

SCHEME = 'scrypt-v1'
DUMMY_SALT = bytes(16)
DUMMY_DIGEST = bytes(32)


def derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=3,
                          maxmem=64 * 1024 * 1024, dklen=32)


def hash_password(password: str) -> str:
    salt = token_bytes(16)
    return f'{SCHEME}${salt.hex()}${derive(password, salt).hex()}'


def verify_password(password: str, stored: str | None) -> bool:
    salt, digest = DUMMY_SALT, DUMMY_DIGEST
    valid = False
    if stored:
        try:
            scheme, salt_hex, digest_hex = stored.split('$')
            parsed_salt, parsed_digest = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
            if scheme == SCHEME and len(parsed_salt) == 16 and len(parsed_digest) == 32:
                salt, digest = parsed_salt, parsed_digest
                valid = True
        except ValueError:
            pass
    matches = compare_digest(derive(password, salt), digest)
    return valid and matches
