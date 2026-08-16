from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import math
import time
import uuid
from collections.abc import Mapping
from threading import Lock

import httpx
import requests
from cachetools import TTLCache

logger = logging.getLogger(__name__)


def get_default_headers(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """
    Build default HTTP headers for GenAI/AIA gateway requests.

    Args:
        extra: Optional headers to merge in (takes precedence on key conflicts).

    Returns:
        A dict of HTTP headers.
    """
    base: dict[str, str] = {
        "accept": "*/*",
        "Content-Type": "application/json",
    }
    if extra:
        base.update(dict(extra))
    return base


def _log_response_status(response: httpx.Response) -> None:
    """
    Response hook: logs method, URL, and status code for all requests made via clients.
    """
    try:
        req = response.request
        payload = json.loads(req.content.decode("utf-8"))
        model = payload.get("model")

        logger.info(f"HTTP {req.method} {req.url} -> {response.status_code} {model}")
    except Exception:
        logger.exception("Failed to log response status")


async def _log_response_status_async(response: httpx.Response) -> None:
    """
    Async response hook: logs method, URL, and status code for all requests made via clients.
    """
    _log_response_status(response)


def build_event_hooks(
    response_hooks: list | None = None,
    async_client: bool = False,
) -> dict[str, list]:
    """
    Compose httpx event hooks with default response logger.

    Args:
        response_hooks: Additional response hook callables.
        async_client: Whether to use async hooks (for AsyncClient) or sync hooks (for Client).
    """
    log_hook = _log_response_status_async if async_client else _log_response_status
    hooks = {"response": [log_hook]}
    if response_hooks:
        hooks["response"].extend(response_hooks)
    return hooks


def get_http_client_with_auth_provider(
    *,
    client_id: str,
    client_secret: str,
    verify: bool | None = False,
    timeout: httpx.Timeout | None = 120,
    headers: Mapping[str, str] | None = None,
    response_hooks: list | None = None,
    is_async: bool = False,
) -> httpx.Client | httpx.AsyncClient:
    """
    Create a configured synchronous httpx.Client for GenAI/AIA calls.

    Args:
        verify: TLS verification toggle. Defaults to settings.GENAI_VERIFY_SSL or False.
        timeout: Custom httpx.Timeout. Defaults to a sane per-settings timeout.
        headers: Headers to attach to all requests (merged over defaults).
        response_hooks: Additional response hook callables.

    Returns:
        httpx.Client or httpx.AsyncClient
    """

    merged_headers = get_default_headers(headers)

    if is_async:
        return httpx.AsyncClient(
            auth=get_auth_provider(client_id, client_secret),
            verify=verify,
            timeout=timeout,
            headers=merged_headers,
            event_hooks=build_event_hooks(response_hooks, async_client=True),
        )

    return httpx.Client(
        auth=get_auth_provider(client_id, client_secret),
        verify=verify,
        timeout=timeout,
        headers=merged_headers,
        event_hooks=build_event_hooks(response_hooks, async_client=False),
    )


def build_http_clients(
    client_id: str | None, client_secret: str | None, verify: bool = True
) -> tuple[httpx.Client, httpx.AsyncClient]:

    if client_id and client_secret:
        logger.info("Using LLM auth method client_secret")
        http_client = get_http_client_with_auth_provider(
            client_id=client_id, client_secret=client_secret, verify=verify
        )
        async_http_client = get_http_client_with_auth_provider(
            client_id=client_id, client_secret=client_secret, is_async=True, verify=verify
        )
        return http_client, async_http_client

    logger.info("Using default LLM auth method")
    http_client = httpx.Client(
        verify=verify, event_hooks=build_event_hooks(async_client=False)
    )
    async_http_client = httpx.AsyncClient(
        verify=verify, event_hooks=build_event_hooks(async_client=True)
    )
    return http_client, async_http_client


# Refer to: https://gitlab.dell.com/community/ai-ml-coe/dsx-blueprints/generative-ai/dev-genai-aia-gateway/dev-genai-text-to-text/
class AuthenticationProviderWithClientSideTokenRefresh(httpx.Auth):
    def __init__(self, client_id: str, client_secret: str):
        """
        Initializes the AuthenticationProviderWithTokenRefresh class.
        Initializes the client_id, client_secret, last_refreshed, and valid_until instance variables.
        """
        # Below properties are applicable to OAUTH only
        self.client_id = client_id
        self.client_secret = client_secret
        self.last_refreshed = math.floor(time.time())
        self.valid_until = math.floor(time.time()) - 1
        self.token = None
        self.expires_in = None
        self._lock = Lock()

    def auth_flow(self, request):
        """
        Authenticates a request using either Single Sign-On or Client & Secret based on the value of USE_SSO.

        Parameters:
            request: The request object to authenticate.

        Returns:
            The authenticated request object.
        """
        request.headers["x-correlation-id"] = str(uuid.uuid4())
        request.headers["Authorization"] = f"Bearer {self.get_bearer_token()}"
        yield request

    def get_bearer_token(self):
        """
        Returns the bearer token. If the current token has expired, it generates a new one using the client ID and secret.

        Returns:
            str: The generated or existing bearer token.
        """
        if self._is_expired():
            with self._lock:
                if self._is_expired():
                    logger.info("Generating new AIA Gateway token...")
                    self.last_refreshed = math.floor(time.time())

                    # Getting token and counting the time it took to be retrieved
                    start = time.perf_counter()
                    _resp = client_credentials(self.client_id, self.client_secret)
                    end = time.perf_counter()
                    logger.info(f"Generated a new token in: {end - start:.6f} seconds")

                    self.token = _resp.token
                    self.expires_in = _resp.expires_in
                    self.valid_until = self.last_refreshed + self.expires_in
        else:
            logger.debug(
                f"AIA Gateway Token not expired, using cached token. Current token valid until {self.valid_until}"
            )
        return self.token

    def _is_expired(self):
        """
        Checks if the current time is greater than or equal to the valid_until attribute.

        Returns:
            bool: True if the current time is greater than or equal to valid_until, False otherwise.
        """
        return time.time() >= self.valid_until


_auth_provider_instance = None
_auth_provider_lock = Lock()


def get_auth_provider(client_id: str, client_secret: str):
    global _auth_provider_instance
    if _auth_provider_instance is None:
        with _auth_provider_lock:
            if _auth_provider_instance is None:
                _auth_provider_instance = (
                    AuthenticationProviderWithClientSideTokenRefresh(
                        client_id, client_secret
                    )
                )
    return _auth_provider_instance


TOKEN_CACHE = TTLCache(maxsize=1000, ttl=600)  # 25 minutes


@dataclasses.dataclass
class AccessToken:
    token: str
    token_type: str
    expires_in: int


def client_credentials(client_id: str, client_secret: str) -> AccessToken:
    """
    :param client_id: Client ID from the Dell Identity API Subscriber
    :param client_secret: Client Secret from the Dell Identity API Subscriber
    :return AccessToken
    AccessToken.access_token will contain the value to be used in the Authorization header

    ie.
    Authorization: Bearer <AccessToken.access_token>

    raises AIAAuthException on failure to retrieve an access token
    """
    combined_key = f"{client_id}:{client_secret}"
    hashed_key = hashlib.sha256(combined_key.encode()).hexdigest()
    access_token = TOKEN_CACHE.get(hashed_key)
    if access_token is not None:
        return access_token

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {"grant_type": "client_credentials"}
    req_auth = (client_id, client_secret)
    response = requests.post(
        "https://www.dell.com/di/api/v3/oauth/token",
        headers=headers,
        data=data,
        auth=req_auth,
        timeout=2,
    )

    if response.status_code != 200:
        raise httpx.HTTPStatusError(
            "Error occurred during client_credentials exchange with Dell Identity.",
            request=response.request,
            response=response,
        )

    result = response.json()
    _access_token = result.get("access_token")
    if _access_token is None:
        raise httpx.HTTPStatusError(
            "Error occurred during client_credentials exchange with Dell Identity. No access token is present.",
            request=response.request,
            response=response,
        )

    access_token = AccessToken(
        _access_token, result.get("token_type"), result.get("expires_in")
    )
    TOKEN_CACHE[hashed_key] = access_token
    return access_token
