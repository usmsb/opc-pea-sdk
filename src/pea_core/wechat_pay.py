"""Small WeChat Pay API v3 client shared by the PEA services.

The client deliberately contains no credentials.  Each PEA supplies the
settings from its environment/Kubernetes Secret and can therefore be deployed
independently.  When the settings are incomplete the caller gets a disabled
payment response instead of an accidental live request.
"""

from __future__ import annotations

import base64
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography import x509


class WechatPayError(RuntimeError):
    """A failed request or an invalid WeChat Pay notification."""


@dataclass(frozen=True)
class WechatPayConfig:
    enabled: bool = False
    base_url: str = "https://api.mch.weixin.qq.com"
    mchid: str | None = None
    sp_mchid: str | None = None
    sub_mchid: str | None = None
    appid: str | None = None
    sub_appid: str | None = None
    serial_no: str | None = None
    private_key_file: str | None = None
    api_v3_key: str | None = None
    platform_cert_file: str | None = None
    notify_url: str | None = None
    amount_multiplier: int = 100

    @property
    def merchant_id(self) -> str | None:
        return self.sp_mchid or self.mchid

    @property
    def partner_mode(self) -> bool:
        return bool(self.sp_mchid and self.sub_mchid)

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and self.merchant_id
            and self.appid
            and self.serial_no
            and self.private_key_file
            and self.api_v3_key
            and self.notify_url
            and Path(self.private_key_file).is_file()
        )


def from_settings(settings: Any) -> WechatPayConfig:
    """Build a config from any Pydantic settings object used by a PEA."""

    return WechatPayConfig(
        enabled=bool(getattr(settings, "wechat_pay_enabled", False)),
        base_url=str(getattr(settings, "wechat_pay_base_url", WechatPayConfig.base_url)),
        mchid=getattr(settings, "wechat_pay_mchid", None),
        sp_mchid=getattr(settings, "wechat_sp_mchid", None),
        sub_mchid=getattr(settings, "wechat_sub_mchid", None),
        appid=getattr(settings, "wechat_appid", None),
        sub_appid=getattr(settings, "wechat_pay_sub_appid", None),
        serial_no=getattr(settings, "wechat_pay_serial_no", None),
        private_key_file=getattr(settings, "wechat_pay_private_key_file", None),
        api_v3_key=getattr(settings, "wechat_pay_api_v3_key", None),
        platform_cert_file=getattr(settings, "wechat_pay_platform_cert_file", None),
        notify_url=getattr(settings, "wechat_pay_notify_url", None),
        amount_multiplier=int(getattr(settings, "wechat_pay_amount_multiplier", 100)),
    )


class WechatPayClient:
    def __init__(self, config: WechatPayConfig):
        self.config = config

    @property
    def configured(self) -> bool:
        return self.config.configured

    def _private_key(self):
        if not self.config.private_key_file:
            raise WechatPayError("WECHAT_PAY_PRIVATE_KEY_FILE is not configured")
        try:
            raw = Path(self.config.private_key_file).read_bytes()
            return serialization.load_pem_private_key(raw, password=None)
        except Exception as exc:  # pragma: no cover - depends on deployment secret
            raise WechatPayError("cannot load WeChat Pay private key") from exc

    def _sign(self, message: str) -> str:
        signature = self._private_key().sign(
            message.encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        return base64.b64encode(signature).decode()

    def _authorization(self, method: str, path: str, body: str) -> str:
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(16)
        signature = self._sign(f"{method}\n{path}\n{timestamp}\n{nonce}\n{body}\n")
        return (
            'WECHATPAY2-SHA256-RSA2048 '
            f'mchid="{self.config.merchant_id}",nonce_str="{nonce}",'
            f'signature="{signature}",timestamp="{timestamp}",'
            f'serial_no="{self.config.serial_no}"'
        )

    async def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        headers = {
            "Authorization": self._authorization("POST", path, body),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "opc-pea-wechat-pay/1.0",
        }
        async with httpx.AsyncClient(base_url=self.config.base_url.rstrip("/"), timeout=15) as client:
            response = await client.post(path, content=body.encode(), headers=headers)
        if response.status_code >= 400:
            raise WechatPayError(f"WeChat Pay returned HTTP {response.status_code}: {response.text[:300]}")
        try:
            return response.json()
        except ValueError as exc:
            raise WechatPayError("WeChat Pay returned non-JSON response") from exc

    async def create_prepay(
        self,
        *,
        out_trade_no: str,
        description: str,
        total_cents: int,
        channel: str,
        openid: str | None = None,
        client_ip: str = "127.0.0.1",
    ) -> dict[str, Any]:
        """Create a JSAPI/miniapp, H5 or Native prepay order."""

        if not self.config.configured:
            return {"mode": "disabled", "sandbox": True, "out_trade_no": out_trade_no}
        channel = channel.lower().strip()
        amount = {"total": int(total_cents), "currency": "CNY"}
        common = {
            "description": description[:127],
            "out_trade_no": out_trade_no,
            "notify_url": self.config.notify_url,
            "amount": amount,
        }
        if self.config.partner_mode:
            common = {
                "sp_appid": self.config.appid,
                "sp_mchid": self.config.sp_mchid,
                "sub_mchid": self.config.sub_mchid,
                **common,
            }
            if channel in {"mini", "jsapi"}:
                path = "/v3/pay/partner/transactions/jsapi"
                if not openid:
                    raise WechatPayError("miniapp/JSAPI payment requires openid")
                common["sub_appid"] = self.config.sub_appid or self.config.appid
                common["payer"] = {"sub_openid": openid}
            elif channel == "h5":
                path = "/v3/pay/partner/transactions/h5"
                common["scene_info"] = {"payer_client_ip": client_ip, "h5_info": {"type": "Wap"}}
            else:
                path = "/v3/pay/partner/transactions/native"
        else:
            common = {"appid": self.config.appid, "mchid": self.config.mchid or self.config.sp_mchid, **common}
            if channel in {"mini", "jsapi"}:
                path = "/v3/pay/transactions/jsapi"
                if not openid:
                    raise WechatPayError("miniapp/JSAPI payment requires openid")
                common["payer"] = {"openid": openid}
            elif channel == "h5":
                path = "/v3/pay/transactions/h5"
                common["scene_info"] = {"payer_client_ip": client_ip, "h5_info": {"type": "Wap"}}
            else:
                path = "/v3/pay/transactions/native"
        result = await self._post(path, common)
        return {"mode": channel, "out_trade_no": out_trade_no, **result}

    def client_params(self, prepay_id: str, *, appid: str | None = None) -> dict[str, str]:
        """Build the parameters consumed by wx.requestPayment."""

        app_id = appid or self.config.sub_appid or self.config.appid
        if not app_id:
            raise WechatPayError("WECHAT_APPID is not configured")
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(16)
        package = f"prepay_id={prepay_id}"
        pay_sign = self._sign(f"{app_id}\n{timestamp}\n{nonce}\n{package}\n")
        return {
            "appId": app_id,
            "timeStamp": timestamp,
            "nonceStr": nonce,
            "package": package,
            "signType": "RSA",
            "paySign": pay_sign,
        }

    def decrypt_notification(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Decrypt API v3 resource; signature verification is done by the route."""

        resource = payload.get("resource") or {}
        if not self.config.api_v3_key or len(self.config.api_v3_key.encode()) != 32:
            raise WechatPayError("WECHAT_PAY_API_V3_KEY must be 32 bytes")
        try:
            cipher = base64.b64decode(resource["ciphertext"])
            tag = base64.b64decode(resource["tag"])
            nonce = str(resource["nonce"]).encode()
            associated = str(resource.get("associated_data", "")).encode()
            plain = AESGCM(self.config.api_v3_key.encode()).decrypt(nonce, cipher + tag, associated)
            return json.loads(plain.decode())
        except Exception as exc:
            raise WechatPayError("cannot decrypt WeChat Pay notification") from exc

    def verify_notification(self, headers: Mapping[str, str], raw_body: bytes) -> bool:
        """Verify a callback using the configured platform certificate."""

        cert_file = self.config.platform_cert_file
        signature = headers.get("Wechatpay-Signature") or headers.get("wechatpay-signature")
        timestamp = headers.get("Wechatpay-Timestamp") or headers.get("wechatpay-timestamp")
        nonce = headers.get("Wechatpay-Nonce") or headers.get("wechatpay-nonce")
        serial = headers.get("Wechatpay-Serial") or headers.get("wechatpay-serial")
        if not all((cert_file, signature, timestamp, nonce, serial)):
            return False
        if self.config.platform_cert_file and not Path(self.config.platform_cert_file).is_file():
            return False
        try:
            cert = Path(cert_file).read_bytes()  # type: ignore[arg-type]
            public_key = x509.load_pem_x509_certificate(cert).public_key()
            message = f"{timestamp}\n{nonce}\n{raw_body.decode()}\n".encode()
            public_key.verify(base64.b64decode(signature), message, padding.PKCS1v15(), hashes.SHA256())
            return serial == self._certificate_serial(cert)
        except Exception:
            return False

    @staticmethod
    def _certificate_serial(cert_pem: bytes) -> str:
        cert = x509.load_pem_x509_certificate(cert_pem)
        return format(cert.serial_number, "X")
