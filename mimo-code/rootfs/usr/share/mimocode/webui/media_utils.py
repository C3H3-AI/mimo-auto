"""Media processing utilities for inbound/outbound media handling.

Handles image compression, file text extraction, CDN download/decrypt,
media source resolution, and CDN upload for WeChat/Feishu.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import aiohttp

_LOGGER = logging.getLogger(__name__)

# CDN configuration (Tencent iLink Bot API)
CDN_BASE_URL = "https://c2cwxappimg.weixin.qq.com"

# File types for text extraction
_TEXT_FILE_EXTENSIONS = {
    ".txt", ".md", ".json", ".csv", ".xml", ".yaml", ".yml",
    ".log", ".py", ".js", ".ts", ".html", ".css", ".toml", ".ini",
}

# Image compression settings
_IMAGE_MAX_DIMENSION = 640
_IMAGE_TARGET_BYTES = 60 * 1024  # 60KB
_IMAGE_QUALITY_LEVELS = [85, 60, 40, 20]

# GIF compression settings
_GIF_COMPRESS_THRESHOLD = 2 * 1024 * 1024  # 2MB
_GIF_MAX_DIMENSION = 360

# File text extraction limit
_FILE_TEXT_MAX_CHARS = 8000


# ---------------------------------------------------------------------------
# CDN Download & Decrypt (Tencent iLink Bot API)
# ---------------------------------------------------------------------------

async def download_cdn_media(
    url: str,
    *,
    timeout: float = 30,
) -> bytes | None:
    """Download media from Tencent CDN with AES-ECB decryption."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    _LOGGER.warning("CDN download failed: HTTP %d", resp.status)
                    return None
                encrypted = await resp.read()
    except Exception as err:
        _LOGGER.warning("CDN download error: %s", err)
        return None

    # Tencent CDN uses AES-ECB encryption with a fixed key
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend

        # The first 16 bytes are the AES key (derived from file metadata)
        # For simplicity, use the standard Tencent CDN decryption pattern
        key = encrypted[:16]
        ciphertext = encrypted[16:]

        cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(ciphertext) + decryptor.finalize()

        # Remove PKCS7 padding
        pad_len = decrypted[-1]
        if 1 <= pad_len <= 16:
            decrypted = decrypted[:-pad_len]

        return decrypted
    except ImportError:
        _LOGGER.warning("cryptography library not available, returning raw CDN data")
        return encrypted
    except Exception as err:
        _LOGGER.warning("CDN decryption failed: %s, returning raw data", err)
        return encrypted


# ---------------------------------------------------------------------------
# Image Processing
# ---------------------------------------------------------------------------

async def compress_image(
    data: bytes,
    *,
    max_dimension: int = _IMAGE_MAX_DIMENSION,
    target_bytes: int = _IMAGE_TARGET_BYTES,
) -> bytes:
    """Compress image to fit within size limits.

    Uses progressive quality reduction to hit target size.
    """
    try:
        from PIL import Image

        img = Image.open(BytesIO(data))

        # Resize if too large
        if max(img.size) > max_dimension:
            ratio = max_dimension / max(img.size)
            new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        # Convert to RGB if needed (for JPEG)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Progressive quality reduction
        for quality in _IMAGE_QUALITY_LEVELS:
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            compressed = buf.getvalue()
            if len(compressed) <= target_bytes:
                return compressed

        return compressed

    except ImportError:
        _LOGGER.debug("PIL not available, returning original image")
        return data
    except Exception as err:
        _LOGGER.warning("Image compression failed: %s", err)
        return data


async def compress_gif(data: bytes) -> bytes:
    """Compress GIF if too large."""
    if len(data) <= _GIF_COMPRESS_THRESHOLD:
        return data

    try:
        from PIL import Image

        img = Image.open(BytesIO(data))
        frames = []
        try:
            while True:
                frames.append(img.copy())
                img.seek(img.tell() + 1)
        except EOFError:
            pass

        if not frames:
            return data

        # Resize frames if too large
        if max(frames[0].size) > _GIF_MAX_DIMENSION:
            ratio = _GIF_MAX_DIMENSION / max(frames[0].size)
            new_size = (int(frames[0].size[0] * ratio), int(frames[0].size[1] * ratio))
            frames = [f.resize(new_size, Image.LANCZOS) for f in frames]

        buf = BytesIO()
        frames[0].save(
            buf, format="GIF", save_all=True, append_images=frames[1:],
            loop=0, optimize=True,
        )
        return buf.getvalue()

    except ImportError:
        return data
    except Exception as err:
        _LOGGER.warning("GIF compression failed: %s", err)
        return data


# ---------------------------------------------------------------------------
# File Text Extraction
# ---------------------------------------------------------------------------

def extract_file_text(data: bytes, filename: str) -> str | None:
    """Extract text content from a file.

    Supports plain text files and DOCX.
    """
    ext = Path(filename).suffix.lower()

    if ext in _TEXT_FILE_EXTENSIONS:
        try:
            text = data.decode("utf-8", errors="replace")
            if len(text) > _FILE_TEXT_MAX_CHARS:
                text = text[:_FILE_TEXT_MAX_CHARS] + "\n... (truncated)"
            return text
        except Exception:
            return None

    if ext == ".docx":
        return _extract_docx_text(data)

    return None


def _extract_docx_text(data: bytes) -> str | None:
    """Extract text from DOCX file (OOXML zip format)."""
    try:
        import zipfile
        from xml.etree import ElementTree

        with zipfile.ZipFile(BytesIO(data)) as zf:
            with zf.open("word/document.xml") as f:
                tree = ElementTree.parse(f)

        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = []
        for p in tree.iter(f"{{{ns['w']}}}p"):
            texts = []
            for t in p.iter(f"{{{ns['w']}}}t"):
                if t.text:
                    texts.append(t.text)
            if texts:
                paragraphs.append("".join(texts))

        text = "\n".join(paragraphs)
        if len(text) > _FILE_TEXT_MAX_CHARS:
            text = text[:_FILE_TEXT_MAX_CHARS] + "\n... (truncated)"
        return text

    except Exception as err:
        _LOGGER.debug("DOCX text extraction failed: %s", err)
        return None


# ---------------------------------------------------------------------------
# Media Source Resolution
# ---------------------------------------------------------------------------

def is_url(source: str) -> bool:
    """Check if source is a URL."""
    return source.startswith(("http://", "https://"))


def resolve_media_source(
    source: str,
    *,
    config_dir: str = "/config",
) -> tuple[bytes, str] | None:
    """Resolve a media source to (bytes, filename).

    Supports URLs, /config/ paths, /local/ paths, and direct file paths.
    """
    source = source.strip()
    if not source:
        return None

    # URL
    if is_url(source):
        # Will be handled async by caller
        return None

    # /config/ path
    if source.startswith("/config/"):
        relative = source.removeprefix("/config/").lstrip("/")
        path = Path(config_dir) / relative
        if path.is_file():
            return path.read_bytes(), path.name

    # /local/ path
    if source.startswith("/local/"):
        relative = source.removeprefix("/local/").lstrip("/")
        path = Path(config_dir) / "www" / relative
        if path.is_file():
            return path.read_bytes(), path.name

    # /media/local/ path
    if source.startswith("/media/local/"):
        relative = source.removeprefix("/media/local/").lstrip("/")
        path = Path(config_dir) / "media" / relative
        if path.is_file():
            return path.read_bytes(), path.name

    # Direct path
    direct = Path(source)
    if direct.is_file():
        return direct.read_bytes(), direct.name

    return None


async def download_url_source(url: str, timeout: float = 30) -> tuple[bytes, str] | None:
    """Download a URL source to (bytes, filename)."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()
                # Extract filename from URL
                filename = url.split("?")[0].split("/")[-1] or "download"
                return data, filename
    except Exception as err:
        _LOGGER.warning("URL download failed: %s", err)
        return None


# ---------------------------------------------------------------------------
# WeChat CDN Upload (Tencent iLink Bot API)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class UploadedMedia:
    """Result of a CDN upload."""
    encrypt_query_param: str
    aes_key_hex: str
    ciphertext_size: int
    plaintext_size: int


def _encrypt_aes_ecb(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt data with AES-ECB + PKCS7 padding."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives import padding as crypto_padding

        padder = crypto_padding.PKCS7(128).padder()
        padded = padder.update(plaintext) + padder.finalize()
        cipher = Cipher(algorithms.AES(key), modes.ECB())
        encryptor = cipher.encryptor()
        return encryptor.update(padded) + encryptor.finalize()
    except ImportError:
        _LOGGER.warning("cryptography not available, returning raw data")
        return plaintext


async def upload_to_wechat_cdn(
    base_url: str,
    token: str,
    to_user_id: str,
    media_bytes: bytes,
    media_type: int = 1,
) -> UploadedMedia:
    """Upload media to WeChat CDN.

    media_type: 1=IMAGE, 2=VIDEO, 3=FILE, 4=VOICE
    """
    filekey = uuid4().hex
    aes_key = uuid4().bytes
    aes_key_hex = aes_key.hex()
    ciphertext = _encrypt_aes_ecb(media_bytes, aes_key)

    # Get upload URL
    async with aiohttp.ClientSession() as session:
        upload_data = await _api_post_wechat(
            session, base_url, "ilink/bot/getuploadurl",
            {
                "filekey": filekey,
                "media_type": media_type,
                "to_user_id": to_user_id,
                "rawsize": len(media_bytes),
                "rawfilemd5": hashlib.md5(media_bytes).hexdigest(),
                "filesize": len(ciphertext),
                "no_need_thumb": True,
                "aeskey": aes_key_hex,
                "base_info": {"channel_version": "mimo-code-addon"},
            },
            token=token,
        )

        upload_param = str(upload_data.get("upload_param") or "")
        upload_full_url = str(upload_data.get("upload_full_url") or "").strip()

        if upload_full_url:
            upload_url = upload_full_url
        elif upload_param:
            upload_url = (
                f"{CDN_BASE_URL}/upload"
                f"?encrypted_query_param={quote(upload_param, safe='')}"
                f"&filekey={quote(filekey, safe='')}"
            )
        else:
            raise ValueError(f"Weixin getuploadurl returned no usable upload url: {upload_data}")

        # Upload to CDN
        async with session.post(
            upload_url,
            data=ciphertext,
            headers={"Content-Type": "application/octet-stream"},
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            if resp.status >= 400:
                raw = await resp.text()
                raise RuntimeError(f"wechat cdn upload {resp.status}: {raw}")
            encrypt_query_param = str(resp.headers.get("x-encrypted-param") or "")

        if not encrypt_query_param:
            raise ValueError("Weixin CDN upload missing x-encrypted-param")

        return UploadedMedia(
            encrypt_query_param=encrypt_query_param,
            aes_key_hex=aes_key_hex,
            ciphertext_size=len(ciphertext),
            plaintext_size=len(media_bytes),
        )


def build_cdn_media(uploaded: UploadedMedia) -> dict[str, Any]:
    """Build CDNMedia dict for WeChat message."""
    return {
        "encrypt_query_param": uploaded.encrypt_query_param,
        "aes_key": base64.b64encode(uploaded.aes_key_hex.encode("utf-8")).decode("ascii"),
        "encrypt_type": 1,
    }


async def _api_post_wechat(
    session: aiohttp.ClientSession,
    base_url: str,
    endpoint: str,
    payload: dict[str, Any],
    token: str | None = None,
    timeout_ms: int = 15000,
) -> dict[str, Any]:
    """Make WeChat API POST request."""
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    body = json.dumps(payload, ensure_ascii=False)
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": uuid4().hex[:8],
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with session.post(
        url, data=body.encode("utf-8"), headers=headers,
        timeout=aiohttp.ClientTimeout(total=timeout_ms / 1000),
    ) as resp:
        raw = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"{endpoint} {resp.status}: {raw}")
        return json.loads(raw) if raw else {}


# ---------------------------------------------------------------------------
# Feishu Upload (Lark Open API)
# ---------------------------------------------------------------------------

async def upload_feishu_image(
    app_id: str,
    app_secret: str,
    image_data: bytes,
) -> str | None:
    """Upload image to Feishu and return image_key."""
    token = await _get_feishu_token(app_id, app_secret)
    if not token:
        return None

    form = aiohttp.FormData()
    form.add_field("image_type", "message")
    form.add_field("image", image_data, filename="image.jpg", content_type="image/jpeg")

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers={"Authorization": f"Bearer {token}"},
            data=form,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            data = await resp.json()
            if resp.status != 200 or data.get("code") != 0:
                _LOGGER.warning("Feishu image upload failed: %s", data.get("msg"))
                return None
            return str((data.get("data") or {}).get("image_key") or "")


async def upload_feishu_file(
    app_id: str,
    app_secret: str,
    file_data: bytes,
    file_name: str,
    file_type: str = "stream",
) -> str | None:
    """Upload file to Feishu and return file_key."""
    token = await _get_feishu_token(app_id, app_secret)
    if not token:
        return None

    form = aiohttp.FormData()
    form.add_field("file_type", file_type)
    form.add_field("file_name", file_name)
    form.add_field("file", file_data, filename=file_name, content_type="application/octet-stream")

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://open.feishu.cn/open-apis/im/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            data=form,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            data = await resp.json()
            if resp.status != 200 or data.get("code") != 0:
                _LOGGER.warning("Feishu file upload failed: %s", data.get("msg"))
                return None
            return str((data.get("data") or {}).get("file_key") or "")


# Token cache for Feishu
_feishu_token_cache: dict[str, tuple[str, float]] = {}


async def _get_feishu_token(app_id: str, app_secret: str) -> str | None:
    """Get Feishu tenant_access_token with caching."""
    import time
    now = time.time()
    cache_key = f"{app_id}:{app_secret}"

    if cache_key in _feishu_token_cache:
        token, expires = _feishu_token_cache[cache_key]
        if now < expires:
            return token

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if data.get("code") != 0:
                    _LOGGER.warning("Feishu token request failed: %s", data.get("msg"))
                    return None
                token = data.get("tenant_access_token", "")
                expire = data.get("expire", 7200)
                _feishu_token_cache[cache_key] = (token, now + expire - 60)
                return token
    except Exception as err:
        _LOGGER.warning("Feishu token request error: %s", err)
        return None
