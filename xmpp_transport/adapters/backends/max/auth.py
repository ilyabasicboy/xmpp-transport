import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import aiohttp


OP_HANDSHAKE = 6
OP_CHECK_PASSWORD = 115
OP_QR_START = 288
OP_QR_STATUS = 289
OP_QR_LOGIN = 291

WEB_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def web_client_headers() -> dict[str, str]:
    return {
        "Origin": "https://web.max.ru",
        "User-Agent": WEB_USER_AGENT,
        "Cache-Control": "no-cache",
    }


def create_max_client_session() -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    return aiohttp.ClientSession(connector=connector)


def web_device_handshake(device_id: str, app_version: str) -> dict:
    return {
        "deviceId": device_id,
        "userAgent": {
            "deviceType": "WEB",
            "deviceName": "Chrome",
            "locale": "ru",
            "deviceLocale": "ru",
            "osVersion": "Windows 10",
            "headerUserAgent": WEB_USER_AGENT,
            "appVersion": app_version,
            "screen": "1080x1920 1.0x",
            "timezone": time.tzname[0],
        },
    }


class MaxLoginError(RuntimeError):
    pass


class MaxPasswordRequired(MaxLoginError):
    pass


@dataclass(frozen=True)
class QrLoginChallenge:
    qr_link: str
    expires_at: Optional[datetime]


class MaxPersonalLoginClient:
    def __init__(self, device_id: str, app_version: str):
        self.device_id = device_id
        self.app_version = app_version
        self.seq = 0
        self.session: Optional[aiohttp.ClientSession] = None
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None

    async def connect(self) -> None:
        self.session = create_max_client_session()
        self.ws = await self.session.ws_connect(
            "wss://ws-api.oneme.ru/websocket",
            headers=web_client_headers(),
        )
        await self.request(OP_HANDSHAKE, web_device_handshake(self.device_id, self.app_version))

    async def request(self, opcode: int, payload: dict) -> dict:
        if self.ws is None or self.ws.closed:
            raise MaxLoginError("MAX WebSocket is not connected")

        seq = self.seq
        self.seq += 1
        await self.ws.send_json(
            {
                "ver": 11,
                "cmd": 0,
                "seq": seq,
                "opcode": opcode,
                "payload": payload,
            }
        )

        while True:
            message = await self.ws.receive()
            if message.type != aiohttp.WSMsgType.TEXT:
                raise MaxLoginError("MAX WebSocket closed during authorization")
            frame = json.loads(message.data)
            if frame.get("seq") != seq or frame.get("opcode") != opcode:
                continue

            response = frame.get("payload") or {}
            if frame.get("cmd") != 1 or response.get("error"):
                error = response.get("error") or response.get("localizedMessage") or "request rejected"
                raise MaxLoginError(f"MAX authorization request failed: {error}")
            return response

    async def close(self) -> None:
        if self.ws is not None and not self.ws.closed:
            await self.ws.close()
        if self.session is not None and not self.session.closed:
            await self.session.close()


class MaxQrAuthorizationFlow:
    def __init__(self, device_id: Optional[str] = None, app_version: str = "26.5.10"):
        self.device_id = device_id or str(uuid.uuid4())
        self.client = MaxPersonalLoginClient(self.device_id, app_version)
        self.track_id: Optional[str] = None
        self.password_track_id: Optional[str] = None
        self.polling_interval = 1.0
        self.expires_at: Optional[datetime] = None

    async def start(self) -> QrLoginChallenge:
        await self.client.connect()
        payload = await self.client.request(OP_QR_START, {})
        self.track_id = str(payload.get("trackId") or "")
        qr_link = payload.get("qrLink")
        if not self.track_id or not isinstance(qr_link, str):
            raise MaxLoginError("MAX did not return QR authorization data")
        self.polling_interval = max(float(payload.get("pollingInterval") or 1000) / 1000, 1.0)
        expires_ms = float(payload.get("expiresAt") or 0)
        self.expires_at = (
            datetime.fromtimestamp(expires_ms / 1000, tz=timezone.utc)
            if expires_ms
            else None
        )
        return QrLoginChallenge(qr_link=qr_link, expires_at=self.expires_at)

    async def wait_for_credentials(self) -> tuple[str, str, str]:
        if not self.track_id:
            raise MaxLoginError("QR authorization flow has not been started")

        while self.expires_at is None or datetime.now(timezone.utc) < self.expires_at:
            await asyncio.sleep(self.polling_interval)
            status = await self.client.request(OP_QR_STATUS, {"trackId": self.track_id})
            if not (status.get("status") or {}).get("loginAvailable"):
                continue

            result = await self.client.request(OP_QR_LOGIN, {"trackId": self.track_id})
            if result.get("passwordChallenge"):
                track_id = (result["passwordChallenge"] or {}).get("trackId")
                if not track_id:
                    raise MaxLoginError("MAX requested a password without a track ID")
                self.password_track_id = str(track_id)
                raise MaxPasswordRequired("MAX requested a two-factor password")
            credentials = extract_credentials(result)
            if credentials is None:
                raise MaxLoginError("MAX did not return login credentials")
            token, account_id = credentials
            return token, self.device_id, account_id

        raise MaxLoginError("MAX QR code expired. Send login again for a new code.")

    async def submit_password(self, password: str) -> tuple[str, str, str]:
        if not self.password_track_id:
            raise MaxLoginError("MAX is not waiting for a two-factor password")
        if not password:
            raise MaxLoginError("MAX password is required")
        result = await self.client.request(
            OP_CHECK_PASSWORD,
            {"trackId": self.password_track_id, "password": password},
        )
        credentials = extract_credentials(result)
        if credentials is None:
            raise MaxLoginError("MAX did not return login credentials after password verification")
        token, account_id = credentials
        self.password_track_id = None
        return token, self.device_id, account_id

    async def close(self) -> None:
        await self.client.close()


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value.strip())
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return f"+{digits}" if digits else ""


def extract_credentials(payload: dict) -> Optional[tuple[str, str]]:
    token_attrs = payload.get("tokenAttrs") or {}
    login = token_attrs.get("LOGIN") or {}
    token = login.get("token")
    profile = payload.get("profile") or {}
    contact = profile.get("contact") or profile
    account_id = contact.get("id")
    if isinstance(token, str) and account_id is not None:
        return token, str(account_id)
    return None
