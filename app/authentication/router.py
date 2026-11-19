from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from app.auth import current_user, mint_token
from app.authentication.passwords import hash_password, verify_password
from app.authentication.schemas import AccessToken, Login, Profile, Registration
from app.database import SessionLocal
from app.models import User

class AuthenticationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validate(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                details = [
                    {key: item[key] for key in ('loc', 'msg', 'type')}
                    for item in error.errors()
                ]
                raise HTTPException(422, detail=details) from None

        return validate


router = APIRouter(prefix='/api/auth', tags=['Authentication'], route_class=AuthenticationRoute)
TOKEN_LIFETIME_SECONDS = 3600


@router.post('/register', status_code=201, response_model=Profile)
def register(body: Registration):
    password_hash = hash_password(body.password.get_secret_value())
    with SessionLocal.begin() as session:
        statement = (
            insert(User)
            .values(username=body.username, first_name=body.first_name,
                    last_name=body.last_name, password=password_hash)
            .on_conflict_do_nothing(index_elements=[User.username])
            .returning(User)
        )
        user = session.scalar(statement)
        if user is None:
            raise HTTPException(409, 'Username is already registered.')
        return Profile.model_validate(user)


@router.post('/login', response_model=AccessToken)
def login(body: Login, response: Response):
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.username == body.username))
        user_id, stored = (user.id, user.password) if user is not None else (None, None)
    if not verify_password(body.password.get_secret_value(), stored) or user_id is None:
        raise HTTPException(401, 'Invalid username or password.', headers={'WWW-Authenticate': 'Bearer'})

    token = mint_token(user_id, TOKEN_LIFETIME_SECONDS)
    response.headers['Cache-Control'] = 'no-store'
    return AccessToken(access_token=token, expires_in=TOKEN_LIFETIME_SECONDS)


@router.get('/me', response_model=Profile)
def me(user_id: Annotated[int, Depends(current_user)]):
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(401, 'User no longer exists.', headers={'WWW-Authenticate': 'Bearer'})
        return Profile.model_validate(user)
