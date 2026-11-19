"""Persistent deadline sweeper; works even when no browser remains connected."""
import asyncio
import logging

from sqlalchemy import and_, or_, select

from app.models import utc_now
from app.conduction.models import Attempt
from app.conduction.service import settle
from app.conduction.transactions import transaction

logger = logging.getLogger(__name__)


def sweep():
    with transaction() as session:
        now = utc_now()
        attempts = session.scalars(select(Attempt).where(or_(
            and_(Attempt.status == 'running', Attempt.deadline <= now),
            and_(Attempt.status == 'paused', Attempt.pause_expires_at <= now),
        )))
        for attempt in attempts:
            settle(attempt, now)


async def run():
    while True:
        try:
            await asyncio.to_thread(sweep)
        except Exception:
            logger.exception('Exam deadline sweep failed; will retry')
        await asyncio.sleep(1)
