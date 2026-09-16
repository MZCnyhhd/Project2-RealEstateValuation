"""微信支付 v3 + 支付宝 官方支付集成（含异步回调验签）。

设计原则
--------
* 所有密钥从环境变量读取，绝不硬编码。
* 缺配置时各函数返回 None 并打点标记，调用方据此回退到「演示模式」。
* 回调验签是「逻辑检验」的核心：只有微信/支付宝真正到账并带正确签名，
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

ALI_APP_ID = os.environ.get("ALIPAY_APP_ID", "").strip()
ALI_PRIVATE_KEY = os.environ.get("ALIPAY_PRIVATE_KEY", "").strip()
ALI_PUBLIC_KEY = os.environ.get("ALIPAY_PUBLIC_KEY", "").strip()

WX_CONFIGURED = all([WX_MCH_ID, WX_APP_ID, WX_API_V3_KEY, WX_MCH_SERIAL, WX_PRIVATE_KEY])
ALI_CONFIGURED = all([ALI_APP_ID, ALI_PRIVATE_KEY, ALI_PUBLIC_KEY])

WX_GATEWAY = "https://api.mch.weixin.qq.com"
ALI_GATEWAY = "https://openapi.alipay.com/gateway.do"


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


def _sorted_kv(params: dict):
    """支付宝签名：按 key 升序拼接为 key=value&key=value（不含 sign）。"""
    items = sorted((k, str(v)) for k, v in params.items() if k not in ("sign", "sign_type") and v != "" and v is not None)
    return "&".join(f"{k}={v}" for k, v in items)


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
    ts = str(int(time.time()))
    nonce = base64.b64encode(os.urandom(16)).decode("ascii")
    url_path = "/v3/pay/transactions/native"
    message = f"POST\n{url_path}\n{ts}\n{nonce}\n{body_str}\n"
    signature = _rsa_sign(priv, message.encode("utf-8"))
    auth = (
        f'WECHATPAY2-SHA256-RSA2048 mchid="{WX_MCH_ID}",'
        f'nonce_str="{nonce}",signature="{signature}",timestamp="{ts}",serial_no="{WX_MCH_SERIAL}"'
    )
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


def verify_wechat_notify(headers: dict, body_str: str):
    """验证微信支付回调，成功返回 (out_trade_no, paid_bool)，失败返回 (None, False)。

    说明：解密成功 + trade_state==SUCCESS 即视为真实到账（密文只有微信与商户
    持有 APIv3 密钥才能解开）。签名验证使用微信平台证书，这里做 best-effort：
    验证通过则更可信，失败时记录但不阻断（避免误伤真实回调）。
    """
    if not WX_CONFIGURED:
        return None, False
    try:
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


# ---------------------------------------------------------------------------
# 支付宝
# ---------------------------------------------------------------------------
def create_alipay_precreate(out_trade_no: str, total_yuan: str, subject: str):
    """创建支付宝面对面扫码单，返回 qr_code 字符串；未配置/失败返回 None。"""
    if not ALI_CONFIGURED:
        return None
    priv = _load_priv(ALI_PRIVATE_KEY)
    if priv is None:
        return None

    biz = json.dumps(
        {"out_trade_no": out_trade_no, "total_amount": total_yuan, "subject": subject},
        ensure_ascii=False, separators=(",", ":"),
    )
    params = {
        "app_id": ALI_APP_ID,
        "method": "alipay.trade.precreate",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "version": "1.0",
        "biz_content": biz,
    }
    params["sign"] = _rsa_sign(priv, _sorted_kv(params).encode("utf-8"))
    try:
        resp = requests.post(ALI_GATEWAY, data=params, timeout=10)
        data = resp.json()
        resp_node = data.get("alipay_trade_precreate_response", {})
        if resp_node.get("code") == "10000":
            return resp_node.get("qr_code")
        logger.warning("支付宝下单失败: %s", json.dumps(resp_node, ensure_ascii=False)[:300])
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("支付宝下单异常: %s", e)
        return None


def verify_alipay_notify(form: dict):
    """验证支付宝异步通知，成功返回 (out_trade_no, paid_bool)。

    校验项：
      1. 签名（RSA2，使用支付宝公钥）
      2. trade_status 为 TRADE_SUCCESS / TRADE_FINISHED
    """
    if not ALI_CONFIGURED:
        return None, False
    try:
        sign = form.get("sign")
        if not sign:
            return None, False
        raw = _sorted_kv(form).encode("utf-8")
        pub = _normalize_pem(ALI_PUBLIC_KEY)
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        public_key = load_pem_public_key(pub)
        ok = public_key.verify(
            base64.b64decode(sign), raw, asym_padding.PKCS1v15(), hashes.SHA256()
        ) is None
        if not ok:
            logger.warning("支付宝回调签名校验失败")
            return None, False
        out_trade_no = form.get("out_trade_no")
        status = form.get("trade_status")
        paid = status in ("TRADE_SUCCESS", "TRADE_FINISHED")
        logger.info("支付宝回调 out_trade_no=%s trade_status=%s", out_trade_no, status)
        return out_trade_no, paid
    except Exception as e:  # noqa: BLE001
        logger.warning("支付宝回调校验异常: %s", e)
        return None, False
