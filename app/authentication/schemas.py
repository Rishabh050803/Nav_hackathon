from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, SecretStr, StringConstraints

Username = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class Login(BaseModel, extra='forbid'):
    username: Username
    password: Annotated[SecretStr, StringConstraints(min_length=1, max_length=256)]


class Registration(Login):
    username: Annotated[str, StringConstraints(
        strip_whitespace=True, min_length=3, max_length=255, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.@+-]*$',
    )]
    password: Annotated[SecretStr, StringConstraints(min_length=8, max_length=256)]
    first_name: Name
    last_name: Name | None = None


class Profile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    first_name: str
    last_name: str | None


class AccessToken(BaseModel):
    access_token: str
    token_type: Literal['bearer'] = 'bearer'
    expires_in: int
