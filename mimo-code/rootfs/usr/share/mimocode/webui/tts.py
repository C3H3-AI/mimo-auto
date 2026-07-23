"""Text-to-Speech utilities using Edge TTS.

Provides TTS generation and MP3-to-SILK conversion for WeChat voice messages.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Default TTS voice
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_RATE = "+0%"
DEFAULT_VOLUME = "+0%"
DEFAULT_PITCH = "+0Hz"


def is_edge_tts_available() -> bool:
    """Check if edge-tts library is available."""
    try:
        import edge_tts  # noqa: F401
        return True
    except ImportError:
        return False


async def generate_tts_mp3(
    text: str,
    *,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    volume: str = DEFAULT_VOLUME,
    pitch: str = DEFAULT_PITCH,
) -> bytes | None:
    """Generate MP3 audio from text using Edge TTS.

    Returns MP3 bytes or None if unavailable.
    """
    if not is_edge_tts_available():
        _LOGGER.warning("edge-tts not available, cannot generate TTS")
        return None

    try:
        import edge_tts

        communicate = edge_tts.Communicate(
            text, voice, rate=rate, volume=volume, pitch=pitch
        )

        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]

        if not audio_data:
            _LOGGER.warning("TTS generated empty audio for: %s", text[:50])
            return None

        return audio_data

    except Exception as err:
        _LOGGER.error("TTS generation failed: %s", err)
        return None


async def mp3_to_silk(mp3_bytes: bytes) -> tuple[bytes, int] | None:
    """Convert MP3 bytes to SILK format for WeChat voice messages.

    Returns (silk_bytes, duration_ms) or None if conversion fails.
    Requires ffmpeg and pilk to be installed.
    """
    if not _has_ffmpeg():
        _LOGGER.warning("ffmpeg not available, cannot convert to SILK")
        return None

    try:
        # Write MP3 to temp file
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(mp3_bytes)
            mp3_path = f.name

        # Convert MP3 -> PCM (16kHz mono) via ffmpeg
        pcm_path = mp3_path + ".pcm"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", mp3_path, "-f", "s16le", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1", pcm_path, "-y",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        if proc.returncode != 0 or not os.path.exists(pcm_path):
            _LOGGER.warning("ffmpeg conversion failed")
            _cleanup(mp3_path)
            return None

        # Read PCM data
        with open(pcm_path, "rb") as f:
            pcm_data = f.read()

        # Convert PCM -> SILK via pilk
        try:
            import pilk
            silk_data = pilk.encode(pcm_data, pcm_rate=16000, tencent=True)
            duration_ms = len(pcm_data) // (16000 * 2) * 1000  # 16-bit mono
            _cleanup(mp3_path, pcm_path)
            return silk_data, duration_ms
        except ImportError:
            _LOGGER.warning("pilk not available, cannot convert to SILK")
            _cleanup(mp3_path, pcm_path)
            return None

    except Exception as err:
        _LOGGER.error("MP3 to SILK conversion failed: %s", err)
        return None


def _has_ffmpeg() -> bool:
    """Check if ffmpeg is available."""
    return os.path.exists("/usr/bin/ffmpeg") or os.path.exists("/usr/local/bin/ffmpeg")


def _cleanup(*paths: str) -> None:
    """Remove temp files."""
    for p in paths:
        try:
            if os.path.exists(p):
                os.unlink(p)
        except Exception:
            pass
