"""列车到离站记录的导入、更正，以及扫描受理时的时刻表快照。"""

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from src.models.trains import RunTimetable, TrainCall
from src.utils.time import iso, local_instant, now

_WINDOW_PADDING = timedelta(hours=2)


def _format(value):
    return value.strftime('%Y-%m-%d %H:%M:%S')


def _parse(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def view(item):
    return {'id': item.id, 'platform': item.platform, 'train_no': item.train_no,
            'arrival_at': _format(item.arrival_at), 'departure_at': _format(item.departure_at),
            'revision': item.revision, 'author': item.author,
            'created_at': iso(item.created_at), 'updated_at': iso(item.updated_at)}


def _normalize(raw, index):
    context = f'第 {index} 趟到离站记录'
    if not isinstance(raw, dict):
        raise ValueError(f'{context}格式无效。')
    train_no = raw.get('train_no')
    if not isinstance(train_no, str) or not train_no.strip() or len(train_no.strip()) > 64:
        raise ValueError(f'{context}的车次须为 1 至 64 个字符。')
    try:
        arrival = local_instant(raw.get('arrival_at'))
        departure = local_instant(raw.get('departure_at'))
    except ValueError as error:
        raise ValueError(f'{context}时间无效。{error}。') from None
    if departure < arrival:
        raise ValueError(f'{context}的离站时间早于到站时间，请更正。')
    revision = raw.get('revision')
    if revision is not None and (type(revision) is not int or revision < 0):
        raise ValueError(f'{context}的已知版本须为非负整数或留空。')
    return {'train_no': train_no.strip(), 'arrival_at': arrival, 'departure_at': departure,
            'revision': revision}


def _same(item, call):
    return item.arrival_at == call['arrival_at'] and item.departure_at == call['departure_at']


def _same_submission(left, right):
    return left['arrival_at'] == right['arrival_at'] and left['departure_at'] == right['departure_at']


def submit(db, platform, calls, actor):
    """按车次逐条加锁写入。

    并发提交互不相干的车次时各自保留；同一车次的时间已被他人改写、而本次提交又未带上最新
    版本时记作冲突，不覆盖对方的更正，其余车次仍照常保存。
    """
    if not isinstance(platform, str) or not platform.strip() or len(platform.strip()) > 300:
        raise ValueError('请填写 1 至 300 个字符的站台名称。')
    platform = platform.strip()
    if not isinstance(calls, list) or not calls:
        raise ValueError('请提交至少一趟到离站记录。')
    if len(calls) > 500:
        raise ValueError('一次最多提交 500 趟到离站记录。')
    normalized = [_normalize(raw, index) for index, raw in enumerate(calls, 1)]
    normalized.sort(key=lambda call: call['train_no'])
    outcome = {'platform': platform, 'created': [], 'corrected': [], 'unchanged': [], 'conflicts': []}
    seen = {}
    for call in normalized:
        train_no = call['train_no']
        previous = seen.get(train_no)
        if previous is not None:
            if not _same_submission(previous, call):
                outcome['conflicts'].append({
                    'train_no': train_no, 'reason': '同一请求内该车次出现不一致的时间',
                    'submitted': {'arrival_at': _format(call['arrival_at']),
                                  'departure_at': _format(call['departure_at'])},
                })
            continue
        seen[train_no] = call
        lookup = select(TrainCall).where(TrainCall.platform == platform, TrainCall.train_no == train_no)
        item = db.scalar(lookup)
        if item is None:
            # 新增路径不先按车次加锁，避免对不存在的行取间隙锁而与并列的另一车次互等。
            item = TrainCall(platform=platform, train_no=train_no, arrival_at=call['arrival_at'],
                             departure_at=call['departure_at'], author=actor)
            try:
                with db.begin_nested():
                    db.add(item)
                    db.flush()
            except IntegrityError:
                # 另一事务刚插入同一车次：锁定该行后按更正或冲突处理。
                item = db.scalar(lookup.with_for_update())
                if item is None:
                    raise
            else:
                outcome['created'].append(train_no)
                continue
        else:
            # 已存在的车次先锁定当前行，读到的是最新已提交的版本。
            item = db.scalar(lookup.with_for_update().execution_options(populate_existing=True))
        if _same(item, call):
            outcome['unchanged'].append(train_no)
            continue
        base = call['revision']
        if base is None or base != item.revision:
            outcome['conflicts'].append(_conflict(item, call, base))
            continue
        # 版本号写进 UPDATE 条件，行锁失效或隔离级别不同时也不会覆盖他人的更正。
        changed = db.execute(update(TrainCall)
                             .where(TrainCall.id == item.id, TrainCall.revision == base)
                             .values(arrival_at=call['arrival_at'], departure_at=call['departure_at'],
                                     revision=base + 1, author=actor, updated_at=now()))
        if changed.rowcount != 1:
            current = db.scalar(lookup.with_for_update().execution_options(populate_existing=True))
            outcome['conflicts'].append(_conflict(current, call, base))
            continue
        item.revision = base + 1
        outcome['corrected'].append(train_no)
    db.flush()
    return outcome


def _conflict(item, call, base):
    current = None
    if item is not None:
        current = {'arrival_at': _format(item.arrival_at), 'departure_at': _format(item.departure_at)}
    if base is None:
        reason = '该车次已由他人先行录入且时间不同，请刷新后带当前版本再更正'
    else:
        reason = '该车次已被更正，提交所依据的版本不是最新版本'
    return {
        'train_no': call['train_no'],
        'reason': reason,
        'expected_revision': base,
        'current_revision': item.revision if item is not None else None,
        'current': current,
        'submitted': {'arrival_at': _format(call['arrival_at']),
                      'departure_at': _format(call['departure_at'])},
    }


def platforms(db, fallback=()):
    names = {name for name in db.scalars(select(TrainCall.platform).distinct()) if name}
    for name in fallback:
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return sorted(names)


def listing(db, platform=None):
    query = select(TrainCall).order_by(TrainCall.arrival_at, TrainCall.train_no)
    if platform:
        query = query.where(TrainCall.platform == platform)
    return [view(item) for item in db.scalars(query.limit(500))]


def dataset_window(rows):
    from src.domain.analysis import validate_rows

    readings = validate_rows(rows)
    return readings[0]['platform'], _parse(readings[0]['timestamp']), _parse(readings[-1]['timestamp'])


def capture(db, platform, first, last):
    """受理时把窗口附近的到离站记录固化下来，之后的更正只影响新的扫描。"""
    maintained = db.scalar(select(TrainCall.id).where(TrainCall.platform == platform).limit(1)) is not None
    calls = []
    if maintained:
        query = select(TrainCall).where(
            TrainCall.platform == platform,
            TrainCall.arrival_at <= last + _WINDOW_PADDING,
            TrainCall.departure_at >= first - _WINDOW_PADDING,
        ).order_by(TrainCall.arrival_at, TrainCall.train_no).limit(1000)
        calls = [{
            'train_no': item.train_no,
            'arrival_at': _format(item.arrival_at),
            'departure_at': _format(item.departure_at),
            'revision': item.revision,
        } for item in db.scalars(query)]
    return {'captured_at': iso(now()), 'platform': platform, 'available': maintained, 'calls': calls}


def store(db, run_id, snapshot):
    db.add(RunTimetable(run_id=run_id, snapshot=snapshot, captured_at=now()))


def snapshot_for(db, run_id):
    item = db.get(RunTimetable, run_id)
    return item.snapshot if item else None
