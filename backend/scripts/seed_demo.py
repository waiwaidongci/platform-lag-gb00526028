import json
from datetime import datetime

from sqlalchemy import func, select

from src.config.database import Session
from src.constants.enums import Role
from src.domain.demo import demo_calls, demo_rows
from src.domain.spec import SPEC
from src.models.identity import User
from src.models.source import Dataset
from src.models.trains import TrainCall
from src.services.datasets import save
from src.utils.security import hash_password


def _seed_calls(db):
    added = 0
    for call in demo_calls():
        lookup = select(TrainCall).where(TrainCall.platform == call['platform'],
                                         TrainCall.train_no == call['train_no'])
        if db.scalar(lookup):
            continue
        db.add(TrainCall(platform=call['platform'], train_no=call['train_no'],
                         arrival_at=datetime.fromisoformat(call['arrival_at']),
                         departure_at=datetime.fromisoformat(call['departure_at']),
                         revision=call['revision'], author='seed'))
        added += 1
    return added


def seed():
    with Session.begin() as db:
        for role in Role:
            if not db.get(User, role.value):
                db.add(User(username=role.value, role=role, password_hash=hash_password(f'{role.value}-demo-2611')))
        item, created = save(db, SPEC['default_dataset'], SPEC['sample_name'], demo_rows(), 'seed')
        calls = _seed_calls(db)
        return {'users': db.scalar(select(func.count()).select_from(User)),
                'datasets': db.scalar(select(func.count()).select_from(Dataset)),
                'train_calls': db.scalar(select(func.count()).select_from(TrainCall)),
                'created_train_calls': calls,
                'seed_dataset_id': item.id, 'row_count': item.row_count, 'checksum': item.checksum,
                'created_dataset': created}


if __name__ == '__main__':
    print(json.dumps(seed(), ensure_ascii=False))
