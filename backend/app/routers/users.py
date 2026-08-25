"""User endpoints.
 
Both are unauthenticated on purpose: the switcher needs the list of seeded
users before an identity exists, and the request form needs the provider list
to render its dropdown.
"""
 
from typing import Annotated
 
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
 
from app.db import get_session
from app.models import Role, User
from app.schemas import UserOut
 
router = APIRouter(prefix="/api", tags=["users"])
 
Session = Annotated[AsyncSession, Depends(get_session)]
 
 
@router.get("/users", response_model=list[UserOut])
async def list_users(session: Session):
    """Populates the role switcher."""
    stmt = select(User).order_by(User.role, User.name)
    return list((await session.execute(stmt)).scalars().all())
 
 
@router.get("/providers", response_model=list[UserOut])
async def list_providers(session: Session):
    """Populates the provider dropdown on the request form.
 
    This is how the provider's name reaches the form without the name being
    stored on the appointment: the user picks a name, the form submits the id.
    """
    stmt = select(User).where(User.role == Role.provider).order_by(User.name)
    return list((await session.execute(stmt)).scalars().all())