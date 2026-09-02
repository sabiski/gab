"""Client E-Billing (Digitech Africa) — PAYIN OAuth2 Cognito + e-bills."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("gabpharma.ebilling")

DEFAULT_SCOPES = (
    "ebilling-api/invoice:create ebilling-api/invoice:read "
    "ebilling-api/payment:create ebilling-api/payment:read"
)

ENVIRONMENTS = {
    "lab": {
        "api_v1": "https://lab.billing-easy.net/api/v1/merchant",
        "api_v2": "https://lab.billing-easy.net/api/v2/merchant",
        "checkout": "https://test.billing-easy.net",
        "oauth": "https://lab.billing-easy.net/oauth/token",
    },
    "production": {
        "api_v1": "https://stg.billing-easy.com/api/v1/merchant",
        "api_v2": "https://stg.billing-easy.com/api/v2/merchant",
        "checkout": "https://staging.billing-easy.net",
        "oauth": "https://billing-easy.com/oauth/token",
    },
}

OPERATOR_CODES = {
    "airtel": "airtelmoney",
    "moov": "moovmoney",
    "card": "ORABANK_NG",
}

FINAL_BILL_STATES = frozenset({"paid", "processed"})
FAILED_BILL_STATES = frozenset({"failed", "cancelled", "expired"})


class EbillingError(Exception):
    """Erreur d'appel API E-Billing."""


@dataclass
class EbillingBillResult:
    bill_id: str
    redirect_url: str | None = None
    raw: dict | None = None


def ebilling_configured() -> bool:
    return bool(
        getattr(settings, "EBILLING_CLIENT_ID", "").strip()
        and getattr(settings, "EBILLING_CLIENT_SECRET", "").strip()
    )


def ebilling_environment() -> str:
    env = (getattr(settings, "EBILLING_ENV", "lab") or "lab").strip().lower()
    return env if env in ENVIRONMENTS else "lab"


def _env() -> dict[str, str]:
    return ENVIRONMENTS[ebilling_environment()]


def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    req = Request(url, data=body, method=method, headers=headers or {})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            return json.loads(raw) if raw.strip() else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        logger.error("E-Billing HTTP %s %s: %s", exc.code, url, detail[:500])
        raise EbillingError(f"E-Billing a répondu {exc.code}.") from exc
    except json.JSONDecodeError as exc:
        raise EbillingError("Réponse E-Billing invalide.") from exc


def _access_token() -> str:
    cache_key = f"ebilling:oauth:{ebilling_environment()}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    client_id = settings.EBILLING_CLIENT_ID.strip()
    client_secret = settings.EBILLING_CLIENT_SECRET.strip()
    scopes = getattr(settings, "EBILLING_SCOPES", DEFAULT_SCOPES)

    import base64

    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    body = urlencode(
        {"grant_type": "client_credentials", "scope": scopes}
    ).encode()
    data = _http_request(
        _env()["oauth"],
        method="POST",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        body=body,
    )
    token = data.get("access_token")
    if not token:
        raise EbillingError("Token OAuth2 E-Billing manquant dans la réponse.")
    ttl = int(data.get("expires_in") or 3600) - 120
    cache.set(cache_key, token, timeout=max(60, ttl))
    return token


def _api_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_access_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def payment_system_for_method(method: str) -> str:
    if getattr(settings, "EBILLING_USE_SIMU", False):
        return "SIMU"
    key = (method or "").lower()
    if key in {OPERATOR_CODES["airtel"], "airtel"}:
        return OPERATOR_CODES["airtel"]
    if key in {OPERATOR_CODES["moov"], "moov"}:
        return OPERATOR_CODES["moov"]
    return OPERATOR_CODES.get(key, OPERATOR_CODES["airtel"])


def checkout_url(bill_id: str, *, return_url: str = "", operator: str = "") -> str:
    params: dict[str, str] = {"invoice": bill_id}
    if return_url:
        params["redirect_url"] = return_url
    if operator == OPERATOR_CODES["card"]:
        params["operator"] = operator
        params["redirect"] = "1"
    elif operator and operator not in {OPERATOR_CODES["card"]}:
        params["operator"] = operator
    return f"{_env()['checkout']}?{urlencode(params)}"


def create_ebill(
    *,
    amount: int,
    reference: str,
    payer_email: str,
    payer_name: str,
    payer_msisdn: str,
    description: str,
    return_url: str = "",
    payment_method: str = "",
) -> EbillingBillResult:
    if not payer_email:
        raise EbillingError("E-mail du payeur requis pour E-Billing.")
    if not payer_name:
        payer_name = "Client Gab'Pharma"

    payload: dict[str, Any] = {
        "amount": int(amount),
        "short_description": (description or reference)[:250],
        "external_reference": reference,
        "payer_email": payer_email,
        "payer_name": payer_name[:120],
        "payer_msisdn": (payer_msisdn or "").strip(),
    }
    expiry = getattr(settings, "EBILLING_EXPIRY_PERIOD", None)
    if expiry:
        payload["expiry_period"] = int(expiry)

    url = f"{_env()['api_v1'].rstrip('/')}/e_bills"
    data = _http_request(url, method="POST", headers=_api_headers(), body=json.dumps(payload).encode())
    bill_id = data.get("bill_id") or (data.get("e_bill") or {}).get("bill_id")
    if not bill_id:
        raise EbillingError("E-Billing n'a pas renvoyé de bill_id.")

    flow = (getattr(settings, "EBILLING_FLOW", "redirect") or "redirect").lower()
    operator = payment_system_for_method(payment_method)

    if flow == "ussd_push":
        if not payer_msisdn:
            raise EbillingError("Numéro de téléphone requis pour le paiement USSD.")
        ussd_push(str(bill_id), payer_msisdn=payer_msisdn, payment_system_name=operator)
        return EbillingBillResult(bill_id=str(bill_id), redirect_url=None, raw=data)

    redirect = checkout_url(str(bill_id), return_url=return_url, operator=operator)
    return EbillingBillResult(bill_id=str(bill_id), redirect_url=redirect, raw=data)


def ussd_push(bill_id: str, *, payer_msisdn: str, payment_system_name: str) -> dict[str, Any]:
    url = f"{_env()['api_v2'].rstrip('/')}/e_bills/{bill_id}/ussd_push"
    body = json.dumps(
        {
            "payer_msisdn": payer_msisdn,
            "payment_system_name": payment_system_name,
        }
    ).encode()
    return _http_request(url, method="POST", headers=_api_headers(), body=body)


def parse_callback_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalise le corps du callback PAYIN (form ou JSON)."""
    reference = str(payload.get("reference") or payload.get("external_reference") or "").strip()
    amount_raw = payload.get("amount")
    try:
        amount = int(float(amount_raw)) if amount_raw not in (None, "") else None
    except (TypeError, ValueError):
        amount = None
    state = str(payload.get("state") or payload.get("status") or "").strip().lower()
    return {
        "reference": reference,
        "amount": amount,
        "state": state,
        "bill_id": str(payload.get("billingid") or payload.get("bill_id") or ""),
        "transaction_id": str(payload.get("transactionid") or payload.get("transaction_id") or ""),
        "payment_system": str(payload.get("paymentsystem") or payload.get("payment_system_name") or ""),
        "raw": payload,
    }


def is_paid_state(state: str) -> bool:
    if not state:
        # Callback v1 sans state explicite : validation via référence + montant côté handler.
        return True
    return state in FINAL_BILL_STATES


def is_failed_state(state: str) -> bool:
    return state in FAILED_BILL_STATES


def verify_webhook_signature(
    *,
    method: str,
    path: str,
    raw_body: bytes,
    headers: dict[str, str],
) -> bool:
    """Vérifie X-Signature (callbacks v2 signés). Retourne True si non signé (v1)."""
    signature = headers.get("X-Signature") or headers.get("x-signature")
    key_id = headers.get("X-Key-Id") or headers.get("x-key-id")
    timestamp = headers.get("X-Signature-Timestamp") or headers.get("x-signature-timestamp")
    if not signature or not key_id or not timestamp:
        return True

    signing_keys: dict[str, str] = getattr(settings, "EBILLING_SIGNING_KEYS", {}) or {}
    secret = signing_keys.get(key_id) or getattr(settings, "EBILLING_WEBHOOK_SIGNING_KEY", "")
    if not secret:
        logger.warning("Callback E-Billing signé mais aucune clé configurée (key_id=%s).", key_id)
        return False

    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - ts) > 300:
        return False

    body_hash = hashlib.sha256(raw_body).hexdigest()
    payload = f"{timestamp}.{method.upper()}.{path}.{body_hash}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
