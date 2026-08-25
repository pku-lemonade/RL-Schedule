import json
import os
import pathlib
import socket
from datetime import datetime, timezone

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


DEFAULT_TIMING_LOG = "logs/timing.log"


def _to_jsonable(value):
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if hasattr(value, "tolist"):
        return _to_jsonable(value.tolist())
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _resolve_log_path() -> pathlib.Path:
    path = os.environ.get("THERMAL_TIMING_LOG", DEFAULT_TIMING_LOG)
    return pathlib.Path(path)


def log_timing(event: str, **fields: object) -> None:
    log_path = _resolve_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "event": event,
    }
    payload.update(_to_jsonable(fields))

    line = json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n"
    with open(log_path, "a", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(line)
        handle.flush()
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
