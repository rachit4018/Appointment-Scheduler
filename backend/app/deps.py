"""Identity and authorization.
 
This is the whole auth surface: about thirty lines.
 
There is no login. The assignment asks for a role switcher, which means a
control that is visible at all times and flips which seeded user you are
acting as. In production the identity would come from a session cookie set at
login or a JWT claim; the only thing that changes is where the id is read
from. Every authorization check below stays exactly as written.
 
The id is accepted from either an X-User-Id header (curl, tests) or a
user_id cookie (the browser, set by the switcher). Header wins when both are
present.
"""

from typing import Annotated
from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Role, User

async def get_current_user(
        session: Annotated[AsyncSession, Depends(get_db)],
        x_user_id: Annotated[int | None, Header()]=None,
        user_id: Annotated[int|None, Cookie()]= None,
) -> User:

    raw_id = x_user_id if x_user_id is not None else user_id
    if raw_id is None:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = "No user selected. Pick a user in the role switcher.",

        )

    user = await session.get(User,raw_id)
    if user is None:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = f"No user with id {raw_id}.",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

def require_role(required: Role):
    """Guard for endpoints only one role may call.
 
    Deliberately separate from the UI's decision about which buttons to
    render. Hiding a button is a convenience; this is the rule.
    """
    async def _guard(user: CurrentUser) -> User:
        if user.role is not required:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action is for {required.value}s.",
            )
        return user
 
    return _guard

RequirePatient = Annotated[User, Depends(require_role(Role.patient))]
RequireProvider = Annotated[User, Depends(require_role(Role.provider))]