"""固定采样网格上的配对 Pearson 互相关、列车车况分组及缺测排查。"""

from bisect import bisect_right
from datetime import datetime, timedelta
from math import fsum, hypot
from statistics import fmean

from .spec import SPEC
from ..utils.time import local_instant as _timestamp

_CHANNELS = (("fan_hz", "风机频率"), ("sound_dba", "站台声级"))


def validate_rows(rows):
    """与 CSV 导入共用校验，返回可序列化且按时间排序的记录。"""
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError("请导入至少一条站台采样记录。")
    readings = []
    seen = set()
    equipment = None
    for index, row in enumerate(rows, 1):
        context = f"第 {index} 条站台采样"
        if not isinstance(row, dict):
            raise ValueError(f"{context}格式无效。")
        record = {}
        for field in SPEC["fields"]:
            key, label = field["key"], field["label"]
            value = row.get(key)
            if field["type"] == "string":
                if not isinstance(value, str) or not value.strip() or len(value.strip()) > 300:
                    raise ValueError(f"{context}的「{label}」须为 1 至 300 个字符。")
                value = value.strip()
            elif value is not None and (
                type(value) not in (int, float) or not field["min"] <= value <= field["max"]
            ):
                raise ValueError(f"{context}的「{label}」须为 {field['min']} 至 {field['max']} 的数值或留空。")
            record[key] = value
        try:
            instant = _timestamp(record["timestamp"])
        except ValueError as error:
            raise ValueError(f"{context}时间无效。{error}。") from None
        record["timestamp"] = instant.isoformat(sep=" ")
        if instant in seen:
            raise ValueError(f"{context}的时间 {record['timestamp']} 重复，请保留每个时刻唯一的一行。")
        seen.add(instant)
        row_equipment = (record["platform"], record["fan_id"])
        if equipment is None:
            equipment = row_equipment
        if row_equipment != equipment:
            raise ValueError(f"{context}与首条记录的站台或风机不同，请按站台和风机拆分数据。")
        readings.append(record)
    return sorted(readings, key=lambda record: record["timestamp"])


def _run_parameters(params):
    if not isinstance(params, dict):
        raise ValueError("滞后扫描参数格式无效。")
    values = {}
    for field in SPEC["params"]:
        key = field["key"]
        value = params.get(key, field["default"])
        if type(value) is not int or not field["min"] <= value <= field["max"]:
            raise ValueError(f"「{field['label']}」须为 {field['min']} 至 {field['max']} 的整数。")
        values[key] = value
    return values


def _sample_grid(readings, sample_seconds):
    start = datetime.fromisoformat(readings[0]["timestamp"])
    grid = {}
    for record in readings:
        timestamp = datetime.fromisoformat(record["timestamp"])
        offset = timestamp - start
        slot, remainder = divmod(offset.days * 86400 + offset.seconds, sample_seconds)
        if remainder:
            raise ValueError(f"{record['timestamp']} 未落在 {sample_seconds} 秒网格上，请核对采样间隔和两路时钟。")
        grid[slot] = record
    return start, grid, slot + 1


def _missing_periods(observed, span, start, sample_seconds, channel):
    periods = []
    previous = -1
    for slot in (*observed, span):
        missing = slot - previous - 1
        if missing:
            first = start + timedelta(seconds=(previous + 1) * sample_seconds)
            last = start + timedelta(seconds=(slot - 1) * sample_seconds)
            periods.append({
                "channel": channel,
                "start": first.isoformat(sep=" "),
                "end": last.isoformat(sep=" "),
                "missing_slots": missing,
            })
        previous = slot
    return periods


def _pearson(pairs):
    centered = []
    for values in zip(*pairs):
        # Shift the origin before centering to keep constant readings exactly constant.
        shifted = [value - values[0] for value in values]
        mean = fmean(shifted)
        deviations = [value - mean for value in shifted]
        norm = hypot(*deviations)
        if not norm:
            return None
        centered.append([value / norm for value in deviations])
    cross = fsum(left * right for left, right in zip(*centered))
    return max(-1.0, min(1.0, cross))


def _train_coverage(snapshot, start, span, sample_seconds):
    """把受理时的到离站记录快照换算成覆盖前缀，供按配对覆盖时间判定车况。

    返回 None 表示该批次站台尚未维护到离站记录，无法做车况分组。
    """
    if not isinstance(snapshot, dict) or not snapshot.get("available"):
        return None
    calls = snapshot.get("calls")
    if not isinstance(calls, list):
        return None
    windows = []
    for index, call in enumerate(calls, 1):
        context = f"到离站快照第 {index} 趟"
        if not isinstance(call, dict):
            raise ValueError(f"{context}格式无效，请重新导入并提交扫描。")
        try:
            arrival = datetime.fromisoformat(call["arrival_at"])
            departure = datetime.fromisoformat(call["departure_at"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"{context}的到站或离站时间格式无效，请重新导入并提交扫描。") from None
        if departure < arrival:
            raise ValueError(f"{context}的离站时间早于到站时间，请更正后重新提交扫描。")
        windows.append(((arrival - start).total_seconds(), (departure - start).total_seconds()))
    windows.sort()
    horizon = span * sample_seconds
    merged = []
    for left, right in windows:
        # 观测窗口之外的部分不参与判定，先裁到 [0, 窗口末端]。
        left, right = max(left, 0.0), min(right, float(horizon))
        if right <= left:
            continue
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    # covered[i]：站内时间 [0, i*Δ) 中被任一列车到发区间覆盖的秒数。
    covered = [0.0] * (span + 1)
    index = 0
    completed = 0.0
    for slot in range(span + 1):
        edge = slot * sample_seconds
        while index < len(merged) and merged[index][1] <= edge:
            left, right = merged[index]
            completed += right - left
            index += 1
        active = 0.0
        if index < len(merged):
            left, right = merged[index]
            active = max(0.0, min(right, edge) - left)
        covered[slot] = completed + active
    ends = [right for _, right in merged]
    starts = [left for left, _ in merged]
    return covered, starts, ends


def _classify(covered, starts, ends, left_slot, right_slot, sample_seconds):
    if right_slot == left_slot:
        instant = left_slot * sample_seconds
        index = bisect_right(ends, instant)
        return "有车" if index < len(starts) and starts[index] <= instant else "无车"
    left = left_slot * sample_seconds
    right = right_slot * sample_seconds
    span_covered = covered[right_slot] - covered[left_slot]
    duration = right - left
    if span_covered >= duration - 1e-9:
        return "有车"
    if span_covered <= 1e-9:
        return "无车"
    return "跨车况"


def _group_status(pairs, coefficient, min_pairs):
    if len(pairs) < min_pairs:
        return "配对不足"
    if coefficient is None:
        return "配对内序列恒定"
    return "已计算"


def _lag_row(lag, fan, sound, span, sample_seconds, min_pairs, windows):
    pairs = []
    train = []
    idle = []
    cross = 0
    for slot, value in fan.items():
        shifted = slot + lag
        if shifted not in sound:
            continue
        pair = (value, sound[shifted])
        pairs.append(pair)
        if windows is None:
            continue
        state = _classify(*windows, min(slot, shifted), max(slot, shifted), sample_seconds)
        if state == "有车":
            train.append(pair)
        elif state == "无车":
            idle.append(pair)
        else:
            cross += 1
    count = len(pairs)
    possible = max(span - abs(lag), 0)
    coefficient = _pearson(pairs) if count >= min_pairs else None
    if not possible:
        status = "时间窗无重叠"
    elif count < min_pairs:
        status = "配对不足"
    elif coefficient is None:
        status = "配对内序列恒定"
    else:
        status = "已计算"
    train_coefficient = _pearson(train) if len(train) >= min_pairs else None
    idle_coefficient = _pearson(idle) if len(idle) >= min_pairs else None
    return {
        "lag_samples": lag,
        "lag_seconds": lag * sample_seconds,
        "pair_count": count,
        "possible_pairs": possible,
        "missing_pairs": possible - count,
        "coverage": round(count / possible, 6) if possible else None,
        "pearson_r": coefficient,
        "status": status,
        "train_pair_count": len(train),
        "train_pearson_r": train_coefficient,
        "train_status": _group_status(train, train_coefficient, min_pairs),
        "idle_pair_count": len(idle),
        "idle_pearson_r": idle_coefficient,
        "idle_status": _group_status(idle, idle_coefficient, min_pairs),
        "cross_pair_count": cross,
    }


def _table(title, columns, rows):
    return {"title": title, "columns": [{"key": key, "label": label} for key, label in columns], "rows": rows}


def _interpret(peak, max_lag):
    if peak is None:
        return "没有可判读的相关档位。", "先补齐缺测或选取风机发生调频的时段，再用相同口径复核。"
    delay = peak["lag_seconds"]
    relation = "同向" if peak["pearson_r"] > 0 else "反向" if peak["pearson_r"] < 0 else "无明显线性方向"
    if delay > 0:
        timing = f"候选峰显示声级变化晚于风机 {delay} 秒"
        action = "按这一时间差复看调频指令、风阀动作及列车进出站记录。"
    elif delay < 0:
        timing = f"候选峰显示声级变化早于风机 {-delay} 秒"
        action = "先核对声级计和楼控时钟，再检查列车、客流或其他设备的时间记录。"
    else:
        timing = "候选峰位于零滞后"
        action = "同一采样格的配对达到候选峰，复测时可缩短采样间隔以检查先后。"
    if max_lag and abs(peak["lag_samples"]) == max_lag:
        action += " 当前峰位于扫描边界，可用更长的连续窗口和更宽的滞后范围复核。"
    if abs(peak["pearson_r"]) < 0.5:
        action += " 该档线性关联较弱，先在无列车通过的连续窗口复测。"
    return (
        f"{timing}，{relation}，r={peak['pearson_r']:.4f}（{peak['pair_count']} 对）。",
        action,
    )


def _pick_peak(lags, prefix):
    valid = [row for row in lags if row[f"{prefix}_pearson_r"] is not None]
    return max(valid, key=lambda row: (
        round(abs(row[f"{prefix}_pearson_r"]), 12), row[f"{prefix}_pair_count"],
        -abs(row["lag_samples"]), -row["lag_samples"],
    ), default=None)


def _condition_group(label, prefix, lags, min_pairs, available):
    if not available:
        return {"label": label, "peak": None, "reason": "本批次站台没有可用的到离站记录，无法分组。",
                "max_pairs": 0, "max_pairs_lag": None}
    peak = _pick_peak(lags, prefix)
    maxima = max(
        ((row[f"{prefix}_pair_count"], abs(row["lag_samples"]), row["lag_seconds"]) for row in lags),
        default=(0, 0, None),
        key=lambda item: (item[0], -item[1]),
    )
    if peak is not None:
        return {"label": label, "peak": {
            "lag_samples": peak["lag_samples"], "lag_seconds": peak["lag_seconds"],
            "pearson_r": peak[f"{prefix}_pearson_r"], "pair_count": peak[f"{prefix}_pair_count"],
        }, "reason": None, "max_pairs": maxima[0], "max_pairs_lag": maxima[2]}
    count, _, lag_seconds = maxima
    if count == 0:
        reason = "整个观测窗口内没有该组的完整配对"
    elif count < min_pairs:
        reason = f"最多只有 {count} 对（{lag_seconds:+d} 秒），少于每档 {min_pairs} 对"
    else:
        reason = "达到配对下限的档位内序列恒定，无法计算相关系数"
    return {"label": label, "peak": None, "reason": reason, "max_pairs": count, "max_pairs_lag": lag_seconds}


def _comparison(train, idle, available, cross_pairs, zero):
    if not available:
        return "尚未导入该站台的到离站记录，无法比较有车与无车两组。请先导入并更正记录，再发起新的扫描。"
    parts = []
    for group in (train, idle):
        if group["peak"]:
            peak = group["peak"]
            parts.append(f"{group['label']}组候选峰 {peak['lag_seconds']:+d} 秒（r={peak['pearson_r']:.4f}，{peak['pair_count']} 对）")
        else:
            parts.append(f"{group['label']}组样本不足：{group['reason']}")
    if train["peak"] and idle["peak"]:
        first, second = train["peak"], idle["peak"]
        total = zero["train_pair_count"] + zero["idle_pair_count"]
        difference = first["lag_seconds"] - second["lag_seconds"]
        if difference:
            parts.append(f"两组峰位相差 {abs(difference)} 秒")
        else:
            parts.append(f"两组候选滞后同为 {first['lag_seconds']:+d} 秒")
        strength = abs(first["pearson_r"]) - abs(second["pearson_r"])
        if strength > 0.02:
            parts.append("有车组峰更高，列车进出站抬高了滞后峰，复测应把有车时段单列")
        elif strength < -0.02:
            parts.append("有车组峰反而更低，列车噪声可能掩盖了风机调速与声级的关联")
        else:
            parts.append("两组峰高接近，列车进出站对候选峰的影响有限")
        if total:
            share = first["pair_count"] / total
            other = second["pair_count"] / total
            if share < 0.2:
                parts.append(f"但有车组候选峰只有 {first['pair_count']} 对（占有车与无车配对合计的 {share:.0%}），需再补有车时段")
            elif other < 0.2:
                parts.append(f"但无车组候选峰只有 {second['pair_count']} 对（占有车与无车配对合计的 {other:.0%}），无车基线仍偏薄")
    if cross_pairs:
        parts.append(f"零滞后另有 {cross_pairs} 对跨车况配对，已单列且未计入两组")
    return "；".join(parts) + "。"


def analyze(rows, normalized_params, timetable=None):
    readings = validate_rows(rows)
    options = _run_parameters(normalized_params)
    interval, max_lag, min_pairs = (options[key] for key in ("sample_seconds", "max_lag", "min_pairs"))
    start, grid, span = _sample_grid(readings, interval)
    fan, sound = (
        {slot: record[key] for slot, record in grid.items() if record[key] is not None}
        for key, _ in _CHANNELS
    )
    windows = _train_coverage(timetable, start, span, interval)
    available = windows is not None
    lags = [_lag_row(lag, fan, sound, span, interval, min_pairs, windows)
            for lag in range(-max_lag, max_lag + 1)]
    valid = [row for row in lags if row["pearson_r"] is not None]
    peak = max(valid, key=lambda row: (
        round(abs(row["pearson_r"]), 12), row["pair_count"], -abs(row["lag_samples"]), -row["lag_samples"],
    ), default=None)
    headline, action = _interpret(peak, max_lag)
    zero = lags[max_lag]
    train = _condition_group("有车", "train", lags, min_pairs, available)
    idle = _condition_group("无车", "idle", lags, min_pairs, available)
    comparison = _comparison(train, idle, available, zero["cross_pair_count"], zero)
    gaps = _missing_periods(grid, span, start, interval, "整行未采集")
    for series, (_, label) in zip((fan, sound), _CHANNELS):
        gaps.extend(_missing_periods(series, span, start, interval, label))
    gaps.sort(key=lambda gap: (gap["start"], gap["channel"]))
    first, last = readings[0], readings[-1]
    calls = timetable.get("calls", []) if isinstance(timetable, dict) else []
    metrics = [
        {"label": "候选滞后（秒）", "value": peak["lag_seconds"] if peak else None,
         "detail": "正值表示声级晚于风机"},
        {"label": "候选峰 Pearson r", "value": round(peak["pearson_r"], 6) if peak else None,
         "detail": f"{peak['pair_count']}/{peak['possible_pairs']} 对" if peak else "配对不足或配对内序列恒定"},
        {"label": "有车组候选峰（秒）",
         "value": train["peak"]["lag_seconds"] if train["peak"] else None,
         "detail": (f"r={train['peak']['pearson_r']:.4f}，{train['peak']['pair_count']} 对"
                    if train["peak"] else train["reason"])},
        {"label": "无车组候选峰（秒）",
         "value": idle["peak"]["lag_seconds"] if idle["peak"] else None,
         "detail": (f"r={idle['peak']['pearson_r']:.4f}，{idle['peak']['pair_count']} 对"
                    if idle["peak"] else idle["reason"])},
        {"label": "零滞后配对（有车/无车/跨车况）",
         "value": f"{zero['train_pair_count']} / {zero['idle_pair_count']} / {zero['cross_pair_count']}",
         "detail": "按配对覆盖的整段时间判定"},
        {"label": "零滞后有效配对", "value": zero["pair_count"], "detail": f"理论可配 {span} 对"},
        {"label": "预期采样格", "value": span, "detail": f"实到 {len(grid)} 行，整行未采集 {span - len(grid)} 格"},
        {"label": "风机缺测格", "value": span - len(fan), "detail": "含整行未采集"},
        {"label": "声级缺测格", "value": span - len(sound), "detail": "含整行未采集"},
    ]
    tables = [
        _table("滞后扫描明细", (
            ("lag_samples", "滞后（格）"), ("lag_seconds", "滞后（秒）"), ("pearson_r", "Pearson r"),
            ("pair_count", "有效配对"), ("possible_pairs", "理论配对"), ("missing_pairs", "缺失配对"),
            ("coverage", "配对覆盖率（0–1）"), ("status", "计算状态"),
        ), lags),
        _table("缺测时段（首末采样时刻均含）", (
            ("channel", "采集通道"), ("start", "首个缺测时刻"), ("end", "末个缺测时刻"),
            ("missing_slots", "缺测格数"),
        ), gaps),
    ]
    if available:
        tables.insert(1, _table("分车况配对与相关（每档）", (
            ("lag_samples", "滞后（格）"), ("lag_seconds", "滞后（秒）"),
            ("train_pair_count", "有车配对"), ("train_pearson_r", "有车 Pearson r"),
            ("train_status", "有车状态"), ("idle_pair_count", "无车配对"),
            ("idle_pearson_r", "无车 Pearson r"), ("idle_status", "无车状态"),
            ("cross_pair_count", "跨车况配对"),
        ), lags))
    if calls:
        captured = timetable.get("captured_at")
        tables.append(_table(
            f"本次扫描采用的到离站记录（受理快照{f' {captured}' if captured else ''}，共 {len(calls)} 趟）",
            (("train_no", "车次"), ("arrival_at", "到站时间"), ("departure_at", "离站时间"),
             ("revision", "版本")),
            [{
                "train_no": call.get("train_no"),
                "arrival_at": call.get("arrival_at"),
                "departure_at": call.get("departure_at"),
                "revision": call.get("revision"),
            } for call in calls if isinstance(call, dict)],
        ))
    note = (
        f"{first['platform']} · {first['fan_id']}，{first['timestamp']} 至 {last['timestamp']}，每 {interval} 秒一格。"
        f"风机在 t，声级在 t + 滞后。每档至少 {min_pairs} 对，仅使用两路均有读数的配对。"
    )
    if calls:
        snapshot_at = timetable.get("captured_at")
        note += f"到离站记录取自受理时快照{(' ' + str(snapshot_at)) if snapshot_at else ''}，共 {len(calls)} 趟。"
    elif not available:
        note += "本批次站台尚未导入到离站记录，车况分组不可用。"
    sections = [
        {"title": "列车进出站与滞后峰", "text": f"{comparison} 分组只用于定位复测窗口，因果判断仍需现场验证。"},
        {"title": "现场复核", "text": f"{action} 相关峰用于缩小排查时段，归因仍需现场复测。"},
        {"title": "缺测与交班", "text": (
            "风机和声级的缺测时段各自包含整行未采集的时间格。核对缺测表并回补原始记录后，沿用采样间隔重跑。"
            if gaps else "观测窗口内两路数据完整。交班时记录所用采样间隔、滞后和配对数。"
        )},
    ]
    return {
        "state": "warning" if gaps or len(valid) < len(lags) or not available else "ok",
        "headline": headline,
        "note": note,
        "metrics": metrics,
        "tables": tables,
        "conditions": {
            "available": available,
            "train": train,
            "idle": idle,
            "cross_pairs": zero["cross_pair_count"],
            "comparison": comparison,
        },
        "charts": [
            {"title": "滞后与 Pearson r（仅可计算档位）", "kind": "bar", "series": [
                {"label": f"{row['lag_seconds']:+d} 秒", "value": row["pearson_r"]} for row in valid
            ]},
            {"title": "各滞后有效配对数", "kind": "bar", "series": [
                {"label": f"{row['lag_seconds']:+d} 秒", "value": row["pair_count"]} for row in lags
            ]},
        ] + ([
            {"title": "有车配对 Pearson r（仅可计算档位）", "kind": "bar", "series": [
                {"label": f"{row['lag_seconds']:+d} 秒", "value": row["train_pearson_r"]}
                for row in lags if row["train_pearson_r"] is not None
            ]},
            {"title": "无车配对 Pearson r（仅可计算档位）", "kind": "bar", "series": [
                {"label": f"{row['lag_seconds']:+d} 秒", "value": row["idle_pearson_r"]}
                for row in lags if row["idle_pearson_r"] is not None
            ]},
        ] if available else []),
        "sections": sections,
    }
