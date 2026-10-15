"""Request-bound device evidence, without user delegation or desktop state."""

import json
from typing import Any, Callable, Mapping

import httpx

DeviceProofProvider = Callable[[str, str, Any], Mapping[str, str]]


def merge_request_headers(auth_headers, extra_headers, device_headers):
    """A bound transport owns its evidence; caller casing cannot override it."""
    extras = {key: value for key, value in extra_headers.items()
              if not device_headers or not key.lower().startswith("x-atellagent-device-")}
    return {**auth_headers, **extras, **device_headers}


def request_device_headers(provider, *, base_url, method, url, options):
    """Sign the actual JSON request URL anew for each transport/auth attempt."""
    if provider is None:
        return {}
    destination, gateway = httpx.URL(url), httpx.URL(base_url)
    if ((destination.scheme, destination.host, destination.port) !=
            (gateway.scheme, gateway.host, gateway.port) or destination.userinfo):
        raise ValueError("Device evidence is restricted to the configured gateway origin")
    if options.get("follow_redirects"):
        raise ValueError("Device-bound requests cannot follow redirects")
    if any(options.get(field) is not None for field in ("content", "data", "files")):
        raise ValueError("Device-bound gateway requests require JSON bodies")
    request = httpx.Request(method, url, params=options.get("params"), json=options.get("json"))
    body = json.loads(request.content) if request.content else None
    return dict(provider(request.method, str(request.url), body))
