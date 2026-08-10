"""AES-CBC helpers for Nous payloads (UTF-8 key/IV, Base64 ciphertext)."""

from __future__ import annotations

import base64
import re

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad


def _key_iv(key: str, iv: str) -> tuple[bytes, bytes]:
    key_b = key.encode("utf-8")
    iv_b = iv.encode("utf-8")
    if len(key_b) not in (16, 24, 32):
        raise ValueError(
            f"AES key must be 16/24/32 bytes as UTF-8, got len={len(key_b)} key={key!r}"
        )
    if len(iv_b) != 16:
        raise ValueError(f"AES IV must be 16 bytes as UTF-8, got len={len(iv_b)} iv={iv!r}")
    return key_b, iv_b


def b64decode_flexible(data: str | bytes) -> bytes:
    """Decode Base64 with common whitespace / URL-safe variants tolerated."""
    if isinstance(data, bytes):
        text = data.decode("ascii", errors="ignore")
    else:
        text = data
    text = text.strip().strip('"').strip("'")
    text = re.sub(r"\s+", "", text)
    text = text.replace("-", "+").replace("_", "/")
    pad_len = (-len(text)) % 4
    if pad_len:
        text += "=" * pad_len
    return base64.b64decode(text)


def _bytes_to_text(decrypted: bytes) -> str:
    # Prefer PKCS7 unpad (encrypt side uses PKCS7); else strip NULs / dig out JSON.
    try:
        return unpad(decrypted, AES.block_size).decode("utf-8")
    except (ValueError, KeyError):
        pass
    text = decrypted.rstrip(b"\x00").decode("utf-8", errors="replace").strip()
    # Trailing garbage after NoPadding decrypt — keep from first { to last }
    if "VG029" in text or "{" in text:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]
    return text


def decrypt(key: str, iv: str, b64_ciphertext: str) -> str:
    """Decrypt Base64 AES-CBC ciphertext (NoPadding; strip NULs / extract JSON)."""
    key_b, iv_b = _key_iv(key, iv)
    raw = b64decode_flexible(b64_ciphertext)
    if len(raw) % AES.block_size != 0:
        raise ValueError(
            f"Ciphertext length {len(raw)} is not a multiple of {AES.block_size} "
            f"(b64 len={len(b64_ciphertext)}, sample={b64_ciphertext[:80]!r})"
        )
    cipher = AES.new(key_b, AES.MODE_CBC, iv_b)
    return _bytes_to_text(cipher.decrypt(raw))


def encrypt(key: str, iv: str, plaintext: str | bytes) -> str:
    """Encrypt with AES-CBC + PKCS7 and return Base64."""
    key_b, iv_b = _key_iv(key, iv)
    data = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
    cipher = AES.new(key_b, AES.MODE_CBC, iv_b)
    return base64.b64encode(cipher.encrypt(pad(data, AES.block_size))).decode("ascii")
