from __future__ import annotations

from typing import Any, Type

from ocpp.v16 import call, call_result


def get_call_class(action: str) -> Type[Any]:
    direct = getattr(call, action, None)
    if direct is not None:
        return direct
    payload = getattr(call, f"{action}Payload", None)
    if payload is not None:
        return payload
    raise RuntimeError(f"OCPP call class not found for action: {action}")


def build_call(action: str, **kwargs: Any) -> Any:
    cls = get_call_class(action)
    return cls(**kwargs)


def get_call_result_class(action: str) -> Type[Any]:
    direct = getattr(call_result, action, None)
    if direct is not None:
        return direct
    payload = getattr(call_result, f"{action}Payload", None)
    if payload is not None:
        return payload
    raise RuntimeError(f"OCPP call_result class not found for action: {action}")


def build_call_result(action: str, **kwargs: Any) -> Any:
    cls = get_call_result_class(action)
    return cls(**kwargs)
