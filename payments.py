"""微信支付 v3 官方支付集成（含异步回调验签）。仅支持微信收款。

设计原则
--------
* 所有密钥从环境变量读取，绝不硬编码。
* 缺配置时各函数返回 None 并打点标记，调用方据此回退到「演示模式」。
* 回调验签是「逻辑检验」的核心：只有微信真正到账并带正确签名，
  才会把订单标记为已支付，报告才会解锁。
"""

import os
import json
import time
import base64
import logging

import requests
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置（环境变量）
# ---------------------------------------------------------------------------
WX_MCH_ID = os.environ.get("WXPAY_MCH_ID", "").strip()
WX_APP_ID = os.environ.get("WXPAY_APP_ID", "").strip()
WX_API_V3_KEY = os.environ.get("WXPAY_API_V3_KEY", "").strip()
WX_MCH_SERIAL = os.environ.get("WXPAY_MCH_SERIAL", "").strip()
WX_PRIVATE_KEY = os.environ.get("WXPAY_PRIVATE_KEY", "").strip()

WX_CONFIGURED = all([WX_MCH_ID, WX_APP_ID, WX_API_V3_KEY, WX_MCH_SERIAL, WX_PRIVATE_KEY])

WX_GATEWAY = "https://api.mch.weixin.qq.com"


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
def _normalize_pem(pem_str):
    """环境变量里常把换行写成 \\n，统一还原为真实换行。"""
    if not pem_str:
        return None
    pem = pem_str
    if "\\n" in pem:
        pem = pem.replace("\\n", "\n")
    return pem.encode("utf-8")


def _load_priv(pem_str):
    raw = _normalize_pem(pem_str)
    if not raw:
        return None
    return load_pem_private_key(raw, password=None)


def _rsa_sign(private_key, data: bytes) -> str:
    sig = private_key.sign(data, asym_padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode("ascii")


def _build_auth(method: str, url_path: str, body_str: str = ""):
    """构造微信支付 v3 请求签名头（Authorization）。

    返回形如 `WECHATPAY2-SHA256-RSA2048 mchid=...` 的字符串；私钥缺失返回 None。
    下单与拉取平台证书共用，避免重复拼装。
    """
    priv = _load_priv(WX_PRIVATE_KEY)
    if priv is None:
        return None
    ts = str(int(time.time()))
    nonce = base64.b64encode(os.urandom(16)).decode("ascii")
    message = f"{method}\n{url_path}\n{ts}\n{nonce}\n{body_str}\n"
    signature = _rsa_sign(priv, message.encode("utf-8"))
    return (
        f'WECHATPAY2-SHA256-RSA2048 mchid="{WX_MCH_ID}",'
        f'nonce_str="{nonce}",signature="{signature}",timestamp="{ts}",serial_no="{WX_MCH_SERIAL}"'
    )


# ---------------------------------------------------------------------------
# 微信支付 v3
# ---------------------------------------------------------------------------
def create_wechat_native(out_trade_no: str, total_fen: int, description: str, notify_url: str):
    """创建微信 NATIVE 支付单，返回可被扫码的 code_url；未配置/失败返回 None。"""
    if not WX_CONFIGURED:
        return None
    priv = _load_priv(WX_PRIVATE_KEY)
    if priv is None:
        return None

    body = {
        "appid": WX_APP_ID,
        "mchid": WX_MCH_ID,
        "description": description,
        "out_trade_no": out_trade_no,
        "notify_url": notify_url,
        "amount": {"total": int(total_fen), "currency": "CNY"},
    }
    body_str = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    url_path = "/v3/pay/transactions/native"
    auth = _build_auth("POST", url_path, body_str)
    if auth is None:
        return None
    headers = {
        "Authorization": auth,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "realestate-pay/1.0",
    }
    try:
        resp = requests.post(
            f"{WX_GATEWAY}{url_path}", data=body_str.encode("utf-8"),
            headers=headers, timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("code_url")
        logger.warning("微信下单失败 %s: %s", resp.status_code, resp.text[:300])
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("微信下单异常: %s", e)
        return None


def _wx_decrypt_resource(resource: dict):
    """用 APIv3 密钥解密回调中的 resource，返回明文 dict。"""
    try:
        key = WX_API_V3_KEY.encode("utf-8")
        nonce = resource["nonce"].encode("utf-8")
        ct = base64.b64decode(resource["ciphertext"])
        aad = resource.get("associated_data", "").encode("utf-8") or None
        plain = AESGCM(key).decrypt(nonce, ct, aad)
        return json.loads(plain.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("微信回调解密失败: %s", e)
        return None


# 微信平台证书缓存：serial_no -> 证书 PEM，1 小时刷新一次
_PLATFORM_CERTS = {}
_PLATFORM_CERTS_TS = 0


def _fetch_platform_certs():
    """拉取微信平台证书（用于回调签名验签）。返回 {serial: pem}；失败返回已有缓存或 {}。"""
    global _PLATFORM_CERTS, _PLATFORM_CERTS_TS
    now = time.time()
    if _PLATFORM_CERTS and now - _PLATFORM_CERTS_TS < 3600:
        return _PLATFORM_CERTS
    if not WX_CONFIGURED:
        return _PLATFORM_CERTS
    auth = _build_auth("GET", "/v3/certificates", "")
    if auth is None:
        return _PLATFORM_CERTS
    try:
        resp = requests.get(
            f"{WX_GATEWAY}/v3/certificates",
            headers={"Authorization": auth, "Accept": "application/json",
                     "User-Agent": "realestate-pay/1.0"},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("微信平台证书拉取失败 %s: %s", resp.status_code, resp.text[:300])
            return _PLATFORM_CERTS
        data = resp.json()
        certs = {}
        for item in data.get("data", []):
            serial = item.get("serial_no")
            enc = item.get("encrypt_certificate", {})
            plain = _wx_decrypt_resource(enc)
            if serial and plain and plain.get("certificate"):
                certs[serial] = plain["certificate"]
        if certs:
            _PLATFORM_CERTS = certs
            _PLATFORM_CERTS_TS = now
        return _PLATFORM_CERTS
    except Exception as e:  # noqa: BLE001
        logger.warning("微信平台证书拉取异常: %s", e)
        return _PLATFORM_CERTS


def _verify_signature(headers: dict, body) -> bool:
    """校验微信回调签名头（平台证书 RSA 验签）。headers 大小写不敏感。"""
    h = {k.lower(): v for k, v in headers.items()}
    ts = h.get("wechatpay-timestamp")
    nonce = h.get("wechatpay-nonce")
    sig = h.get("wechatpay-signature")
    serial = h.get("wechatpay-serial")
    if not (ts and nonce and sig and serial):
        logger.warning("微信回调缺少签名头")
        return False
    body_str = body.decode("utf-8") if isinstance(body, bytes) else body
    message = f"{ts}\n{nonce}\n{body_str}\n"
    certs = _fetch_platform_certs()
    cert_pem = certs.get(serial)
    if not cert_pem:
        logger.warning("微信回调平台证书未命中 serial=%s", serial)
        return False
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_x509_certificate
        from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
        cert = load_pem_x509_certificate(cert_pem.encode("utf-8"))
        cert.public_key().verify(
            base64.b64decode(sig), message.encode("utf-8"),
            asym_padding.PKCS1v15(), hashes.SHA256(),
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("微信回调签名验签失败: %s", e)
        return False


def verify_wechat_notify(headers: dict, body):
    """验证微信支付回调：先验签（平台证书），再解密 resource 取交易状态。

    成功返回 (out_trade_no, paid_bool)，失败返回 (None, False)。
    仅在 WX_CONFIGURED 时启用；演示模式（未配置）不会收到官方回调，直接返回 (None, False)。
    """
    if not WX_CONFIGURED:
        return None, False
    try:
        if not _verify_signature(headers, body):
            return None, False
        body_str = body.decode("utf-8") if isinstance(body, bytes) else body
        payload = json.loads(body_str)
        resource = payload.get("resource", {})
        plain = _wx_decrypt_resource(resource)
        if not plain:
            return None, False
        out_trade_no = plain.get("out_trade_no")
        paid = plain.get("trade_state") == "SUCCESS"
        logger.info("微信回调 out_trade_no=%s trade_state=%s", out_trade_no, plain.get("trade_state"))
        return out_trade_no, paid
    except Exception as e:  # noqa: BLE001
        logger.warning("微信回调解析失败: %s", e)
        return None, False


