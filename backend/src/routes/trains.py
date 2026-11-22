from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config.database import connection
from src.constants.enums import Role
from src.controllers.auth import allow, current
from src.models.identity import User
from src.models.source import Dataset
from src.schemas.requests import TrainTimesRequest
from src.services import timetable
from src.services.audit import record

router = APIRouter()


def _dataset_platforms(db):
    names = []
    for rows in db.scalars(select(Dataset.rows).order_by(Dataset.id.desc()).limit(100)):
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            name = rows[0].get('platform')
            if isinstance(name, str):
                names.append(name.strip())
    return names


@router.get('/trains')
def trains(platform: str | None = Query(None, max_length=300),
           db: Session = Depends(connection), actor: User = Depends(current)):
    return {'platforms': timetable.platforms(db, _dataset_platforms(db)),
            'calls': timetable.listing(db, platform)}


@router.post('/trains')
def correct(body: TrainTimesRequest, db: Session = Depends(connection),
            actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    outcome = timetable.submit(db, body.platform, [call.model_dump() for call in body.calls], actor.username)
    record(db, actor.username, 'timetable.corrected', outcome['platform'],
           created=outcome['created'], corrected=outcome['corrected'],
           unchanged=outcome['unchanged'], conflicts=[item['train_no'] for item in outcome['conflicts']])
    db.commit()
    return outcome