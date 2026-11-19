"""Shared signed bearer-token adapter and local token-minting CLI."""
import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.database import SessionLocal
from app.models import User

bearer = HTTPBearer(auto_error=False)


def secret() -> bytes:
    value = os.environ.get('AUTH_TOKEN_SECRET', '')
    if len(value) < 32:
        raise HTTPException(503, 'Configure AUTH_TOKEN_SECRET with at least 32 characters')
    return value.encode()


def mint_token(user_id: int, ttl_seconds: int = 3600) -> str:
    if user_id <= 0 or ttl_seconds <= 0:
        raise ValueError('User ID and token lifetime must be positive')
    payload = base64.urlsafe_b64encode(json.dumps(
        {'sub': user_id, 'exp': int(time.time()) + ttl_seconds}, separators=(',', ':')
    ).encode()).decode().rstrip('=')
    signature = hmac.new(secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f'{payload}.{signature}'


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> int:
    if credentials is None:
        raise HTTPException(401, 'Bearer token required', headers={'WWW-Authenticate': 'Bearer'})
    key = secret()
    try:
        payload, signature = credentials.credentials.split('.')
        expected = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError('signature')
        data = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        user_id, expires = data['sub'], data['exp']
        if type(user_id) is not int or user_id <= 0 or type(expires) is not int or expires <= time.time():
            raise ValueError('claims')
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise HTTPException(401, 'Invalid or expired token', headers={'WWW-Authenticate': 'Bearer'}) from None
    with SessionLocal() as session:
        if session.scalar(select(User.id).where(User.id == user_id)) is None:
            raise HTTPException(401, 'User no longer exists')
    return user_id


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Mint a trusted local development bearer token')
    parser.add_argument('user_id', type=int)
    parser.add_argument('--ttl', type=int, default=3600)
    args = parser.parse_args()
    with SessionLocal() as session:
        if session.get(User, args.user_id) is None:
            parser.error('User does not exist')
    print(mint_token(args.user_id, args.ttl))
