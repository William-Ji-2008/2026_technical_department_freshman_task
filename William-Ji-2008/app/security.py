# -*- coding: utf-8 -*-
"""
安全模块：密码哈希 + JWT 令牌（全部用标准库实现）
================================================

一、密码哈希
   用 hashlib.pbkdf2_hmac 做 PBKDF2-HMAC-SHA256，每个用户一个随机盐。
   数据库里存的是这样一串文本：

       pbkdf2_sha256$120000$<盐的十六进制>$<摘要的十六进制>

   为什么不能用 MD5 或 SHA1 直接存？
   因为这两种算法太快了，攻击者每秒能试几亿次；而且不加盐时，
   两个用户密码相同、哈希就相同，彩虹表一查就出来。
   PBKDF2 通过"加盐 + 反复迭代"把每一次尝试的成本抬高。

二、JWT 令牌
   JWT 一共三段，用 "." 连接：头部.载荷.签名。前两段是 Base64URL 编码的 JSON。

       头部 {"alg":"HS256","typ":"JWT"}
       载荷 {"sub":"1","exp":1791300000,"role":"admin"}
       签名 HMAC-SHA256(头部.载荷, 密钥)

   服务端不需要保存会话，只要校验签名，就能确认"这令牌是我签发的、且没被改过"。
   注意：载荷只是编码，不是加密，任何人都能解开看内容，
   所以千万不要往里面放密码之类的敏感信息。
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Optional

from app import config

# PBKDF2 迭代次数：越高越安全，但登录也越慢。12 万次约几十毫秒，比较均衡。
PBKDF2_ITERATIONS = 120_000
SALT_BYTES = 16
PASSWORD_PREFIX = "pbkdf2_sha256"


# ---------------------------------------------------------------------------
# 一、密码哈希
# ---------------------------------------------------------------------------

def hash_password(raw_password: str) -> str:
    """把明文密码变成可以安全入库的哈希字符串。"""
    # secrets 是密码学安全的随机数模块，比 random 更适合生成盐
    salt = secrets.token_bytes(SALT_BYTES)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        raw_password.encode("utf-8"),   # 统一用 UTF-8，支持中文密码
        salt,
        PBKDF2_ITERATIONS,
    )

    # 用 $ 分隔字段，将来升级算法时能靠前缀区分新旧格式
    return f"{PASSWORD_PREFIX}${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(raw_password: str, stored: str) -> bool:
    """校验明文密码是否与数据库里的哈希匹配。"""
    try:
        prefix, iterations, salt_hex, digest_hex = stored.split("$")
        if prefix != PASSWORD_PREFIX:
            return False

        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            raw_password.encode("utf-8"),
            salt,
            int(iterations),
        )

        # 用恒定时间比较函数，避免通过响应耗时去猜密码
        return hmac.compare_digest(actual, expected)
    except (ValueError, AttributeError, TypeError):
        # 数据被改坏、字段数量不对等情况，一律当作密码错误
        return False


# ---------------------------------------------------------------------------
# 二、JWT 令牌
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    """Base64URL 编码，并去掉末尾的等号（JWT 规范要求）。"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    """Base64URL 解码：把去掉的等号补回来，否则 Python 会报错。"""
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(message: bytes) -> str:
    """用密钥对消息做 HMAC-SHA256 签名，返回十六进制字符串。"""
    return hmac.new(
        config.SECRET_KEY.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def create_access_token(subject: Any, extra: Optional[dict] = None) -> str:
    """
    签发令牌。

    参数：
        subject: 主体，一般传用户 ID，会写进标准字段 sub
        extra  : 想额外放进去的字段，例如 {"role": "admin"}
    """
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": str(subject),
        "iat": int(time.time()),                                     # 签发时间
        "exp": int(time.time()) + config.TOKEN_EXPIRE_MINUTES * 60,  # 过期时间
    }
    if extra:
        payload.update(extra)

    # separators 去掉多余空格、ensure_ascii=False 让中文保持原样。
    # 这两个参数会直接影响被签名的字节内容，所以签发和验签必须完全一致。
    header_b64 = _b64url_encode(
        json.dumps(header, separators=(",", ":")).encode("utf-8")
    )
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )

    body = f"{header_b64}.{payload_b64}"
    return f"{body}.{_sign(body.encode('ascii'))}"


def decode_access_token(token: str) -> Optional[dict]:
    """
    校验并解析令牌。

    返回载荷字典；令牌被篡改、格式不对或已过期时返回 None。
    """
    try:
        header_b64, payload_b64, signature = token.split(".")
    except ValueError:
        return None

    # 1. 先验签：签名对不上说明令牌被人改过，直接拒绝
    expected = _sign(f"{header_b64}.{payload_b64}".encode("ascii"))
    if not hmac.compare_digest(expected, signature):
        return None

    # 2. 再解析载荷
    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None

    # 3. 最后检查是否过期
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None

    return payload
