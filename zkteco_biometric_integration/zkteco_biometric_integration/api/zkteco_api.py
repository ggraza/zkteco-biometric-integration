from frappe import _
from frappe.utils import get_datetime, add_to_date

from .api_connector import APIConnector
from zkteco_biometric_integration.zkteco_biometric_integration.doctype.zkteco_biometric_settings.zkteco_biometric_settings import (
    ZKTecoBiometricSettings,
)
from ..utils import log_throw_error

from collections.abc import Iterable

TOKEN_ENDPOINT = "/jwt-api-token-auth/"
TRANSACTIONS_ENDPOINT = "/iclock/api/transactions/"


def get_token(settings: "ZKTecoBiometricSettings") -> str:
    payload = {"username": settings.username, "password": settings.get_password()}
    response = (
        APIConnector(settings_doc=settings)
        .set_http_method("POST")
        .set_base_url(settings.url)
        .set_endpoint(TOKEN_ENDPOINT)
        .set_payload(payload)
        .make_remote_call()
    )

    token = response.get("token") if response else None
    if not token:
        log_throw_error(_("ZKTeco did not return an authentication token"))

    settings.db_set(
        {
            "token": token,
            "issued_at": get_datetime(),
            "expiry": add_to_date(get_datetime(), days=1),
        }
    )
    settings.reload()

    return token


def get_transactions(
    settings: "ZKTecoBiometricSettings",
    headers: dict,
    params: dict,
    end_time: str,
) -> Iterable[dict]:
    has_transactions = False
    next_url = None
    visited_urls = set()

    while True:
        connector = (
            APIConnector(settings_doc=settings)
            .set_http_method("GET")
            .set_headers(headers)
            .set_base_url(settings.url)
        )

        if next_url:
            connector.set_endpoint(next_url).set_params(None)
        else:
            connector.set_endpoint(TRANSACTIONS_ENDPOINT).set_params(params)

        visited_urls.add(connector.absolute_url)

        response = connector.make_remote_call()

        data = response.get("data") if response else None
        if not data:
            break

        has_transactions = True

        yield from data

        next_url = response.get("next")
        if not next_url or next_url in visited_urls:
            break

    if has_transactions:
        settings.db_set("last_fetched_time", end_time, update_modified=False)
