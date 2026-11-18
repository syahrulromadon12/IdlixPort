"""Only source-domain cookies cross the private worker pipe, never public JSON."""

import time
from http.cookiejar import Cookie

from .errors import AppError


def source_cookie(cookie, host):
    domain = cookie.domain.lstrip(".")
    matches = host == domain or (cookie.domain_specified and host.endswith("." + domain))
    return matches and not cookie.is_expired()


def encode_cookies(jar, host):
    result = []
    for cookie in jar:
        if source_cookie(cookie, host):
            result.append(
                {
                    key: getattr(cookie, key)
                    for key in (
                        "name",
                        "value",
                        "domain",
                        "domain_specified",
                        "domain_initial_dot",
                        "path",
                        "path_specified",
                        "secure",
                        "expires",
                        "discard",
                    )
                }
            )
    if len(result) > 128:
        raise AppError("COOKIE_UNAVAILABLE")
    return result


def decode_cookies(values):
    for value in values:
        yield Cookie(
            version=0,
            port=None,
            port_specified=False,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
            **value,
        )


def usable(values):
    return [value for value in values if value["expires"] is None or value["expires"] > time.time()]
