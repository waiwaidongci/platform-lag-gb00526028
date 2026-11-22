from datetime import UTC, datetime


def now():
    return datetime.now(UTC).replace(tzinfo=None)


def iso(value):
    return value.replace(tzinfo=UTC).isoformat(timespec='seconds') if value else None


def local_instant(value):
    """解析站内本地时间 YYYY-MM-DD HH:MM:SS，不接受时区或小数秒。"""
    try:
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError('请填写本地时间 YYYY-MM-DD HH:MM:SS') from None
    if not isinstance(value, str) or result.isoformat(sep=' ') != value.replace('T', ' ') or result.tzinfo is not None:
        raise ValueError('请填写本地时间 YYYY-MM-DD HH:MM:SS，精确到整秒')
    return result
