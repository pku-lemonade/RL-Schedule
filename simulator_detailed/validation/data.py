"""Small checked JSON accessors; counters never pass through floating point."""

import json
import math
from typing import cast

from pydantic import JsonValue

Data = dict[str, JsonValue]


def obj(value: object) -> Data:
    if not isinstance(value, dict):
        raise TypeError("expected a JSON object")
    return cast(Data, value)


def array(value: object) -> list[JsonValue]:
    if not isinstance(value, list):
        raise TypeError("expected a JSON array")
    return cast(list[JsonValue], value)


def rows(value: object) -> list[Data]:
    return [obj(item) for item in array(value)]


def text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("expected text")
    return value


def integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError("expected an exact integer")
    return value


def number(value: object) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("expected a finite number")
    return value


def parse(value: str) -> Data:
    return obj(json.loads(value))


def key(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)
