from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException, Request
from pwdlib import PasswordHash

from app.domain import User

PASSWORD_HASH = PasswordHash.recommended()


class TokenManager:
    def __init__(self, secret: str, ttl_minutes: int = 60) -> None:
        self.secret = secret
        self.ttl_minutes = ttl_minutes

    def issue(self, user: User) -> str:
        now = datetime.now(timezone.utc)
        return jwt.encode(
            {
                "sub": user.id,
                "username": user.username,
                "role": user.role,
                "iat": now,
                "exp": now + timedelta(minutes=self.ttl_minutes),
            },
            self.secret,
            algorithm="HS256",
        )

    def decode(self, token: str) -> dict:
        try:
            return jwt.decode(token, self.secret, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=401, detail="AUTH_TOKEN_INVALID") from exc


def current_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="AUTH_REQUIRED")
    payload = request.app.state.token_manager.decode(authorization[7:])
    user = request.app.state.repository.get_user(str(payload.get("sub", "")))
    if not user or user.status != "active":
        raise HTTPException(status_code=401, detail="AUTH_USER_INACTIVE")
    return user


def require_roles(*roles: str):
    def dependency(user: User = Depends(current_user)) -> User:  # noqa: B008
        if user.role not in set(roles):
            raise HTTPException(status_code=403, detail="AUTH_FORBIDDEN")
        return user

    return dependency
