"""Codec de frames de tela (JPEG via Pillow).

Motion-JPEG: cada frame é um JPEG independente. Simples e robusto para suporte
remoto em LAN; sem estado entre frames (nenhum keyframe a perder numa queda).
O Pillow é importado de forma preguiçosa e com mensagem clara se faltar, para o
resto da aplicação (mouse/teclado) continuar funcionando sem a dependência.
"""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)


class CodecError(RuntimeError):
    """Pillow ausente ou falha ao codificar/decodificar um frame."""


def _require_pillow():  # type: ignore[no-untyped-def]
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - caminho de dependência ausente
        raise CodecError(
            "Pillow é necessário para o acesso remoto de tela; instale com 'pip install Pillow'"
        ) from exc
    return Image


def pillow_available() -> bool:
    """True se o Pillow está instalado (para pular testes/telemetria)."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def encode_jpeg(rgb: bytes, width: int, height: int, quality: int, scale: float = 1.0) -> bytes:
    """Codifica um buffer RGB (24 bits, sem padding) em JPEG.

    ``scale`` em ``(0, 1]`` reduz a resolução antes de codificar, cortando
    banda. ``quality`` é o parâmetro JPEG (1-95).
    """
    Image = _require_pillow()
    try:
        image = Image.frombytes("RGB", (width, height), rgb)
        if scale < 1.0:
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            image = image.resize(new_size, Image.BILINEAR)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality)
        return buffer.getvalue()
    except (ValueError, OSError) as exc:
        raise CodecError(f"falha ao codificar frame JPEG: {exc}") from exc


def decode_image(blob: bytes):  # type: ignore[no-untyped-def]
    """Decodifica bytes JPEG num ``PIL.Image`` (usado pelo viewer)."""
    Image = _require_pillow()
    try:
        image = Image.open(io.BytesIO(blob))
        image.load()
        return image
    except (ValueError, OSError) as exc:
        raise CodecError(f"falha ao decodificar frame JPEG: {exc}") from exc
