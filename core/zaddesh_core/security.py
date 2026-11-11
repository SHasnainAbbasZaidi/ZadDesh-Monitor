from __future__ import annotations

import base64
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Request
import jwt
from zaddesh_core.config import settings

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash password using Argon2id."""
    return _ph.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against Argon2id hash."""
    try:
        return _ph.verify(hashed_password, plain_password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def create_access_token(
    data: dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a signed JWT access token."""
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.security.jwt_expiration_minutes)

    to_encode.update({"exp": int(expire.timestamp()), "iat": int(now.timestamp())})
    encoded_jwt = jwt.encode(
        to_encode,
        settings.security.jwt_secret,
        algorithm=settings.security.jwt_algorithm,
    )
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict[str, Any]]:
    """Decode and validate a JWT access token."""
    try:
        payload = jwt.decode(
            token,
            settings.security.jwt_secret,
            algorithms=[settings.security.jwt_algorithm],
            options={"require": ["exp", "iat", "sub", "user_id"]},
        )
        return payload
    except jwt.PyJWTError:
        return None


def generate_api_token() -> tuple[str, str, str]:
    """
    Generate an API token.
    Returns: (raw_token, prefix, token_hash)
    """
    raw_secret = secrets.token_hex(24)
    prefix = f"zds_{raw_secret[:6]}"
    raw_token = f"zds_{raw_secret}"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    return raw_token, prefix, token_hash


def hash_token(raw_token: str) -> str:
    """Hash an API or collector token with SHA-256."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def allows_secret_transport(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    return (
        request.client is not None
        and request.client.host in {"127.0.0.1", "::1"}
        and request.url.hostname in {"localhost", "127.0.0.1", "::1"}
        and not any(
            header == "forwarded" or header.startswith("x-forwarded-")
            for header in request.headers
        )
    )


def _get_aes_key() -> bytes:
    key_hex = settings.security.encryption_key
    if not key_hex:
        raise RuntimeError("Set ZADDESH_SECURITY__ENCRYPTION_KEY before storing or reading credentials")
    # Normalize to 32 bytes (256-bit AES)
    key_bytes = hashlib.sha256(key_hex.encode("utf-8")).digest()
    return key_bytes


def encrypt_secret(plain_text: str) -> str:
    """Encrypt secret using AES-256-GCM. Returns base64 string with nonce + ciphertext."""
    if not plain_text:
        return ""
    key = _get_aes_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plain_text.encode("utf-8"), None)
    payload = nonce + ciphertext
    return base64.b64encode(payload).decode("utf-8")


def decrypt_secret(encrypted_text: str) -> str:
    """Decrypt secret encrypted with AES-256-GCM."""
    if not encrypted_text:
        return ""
    raw = base64.b64decode(encrypted_text, validate=True)
    nonce = raw[:12]
    ciphertext = raw[12:]
    return AESGCM(_get_aes_key()).decrypt(nonce, ciphertext, None).decode("utf-8")
