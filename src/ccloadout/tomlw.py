from __future__ import annotations
import datetime as _dt, json, math

def _string(text: str) -> str:
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")

def _key(key: str) -> str:
    return _string(str(key))

def _value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return repr(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_key(k)} = {_value(v)}" for k, v in value.items()) + "}"
    raise TypeError(f"cannot write {type(value).__name__} as TOML")

def _is_table_array(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)

def _table(path: list[str], table: dict, out: list[str]) -> None:
    scalars = [(k, v) for k, v in table.items()
               if not isinstance(v, dict) and not _is_table_array(v)]
    for k, v in scalars:
        out.append(f"{_key(k)} = {_value(v)}")
    for k, v in table.items():
        if isinstance(v, dict):
            sub = [*path, _key(k)]
            out.append(f"\n[{'.'.join(sub)}]")
            _table(sub, v, out)
        elif _is_table_array(v):
            sub = [*path, _key(k)]
            for item in v:
                out.append(f"\n[[{'.'.join(sub)}]]")
                _table(sub, item, out)

def dumps(data: dict) -> str:
    """Serialise a tomllib-shaped dict back to TOML text that tomllib reads as the same dict.

    Every key is quoted, scalars come before subtables, a list of tables becomes an array of
    tables, and strings use JSON escapes, which TOML basic strings accept. Comments and the
    original layout are not kept.
    """
    out: list[str] = []
    _table([], data, out)
    return "\n".join(out).lstrip("\n") + "\n"
