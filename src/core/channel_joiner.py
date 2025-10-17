import logging
import asyncio
import re
from typing import Optional, Tuple, Dict
from datetime import datetime, timedelta

from pyrogram import Client
from pyrogram.types import Chat
from pyrogram.errors import (
    FloodWait, InviteHashExpired, InviteHashInvalid, 
    UserAlreadyParticipant, ChannelPrivate
)

logger = logging.getLogger(__name__)

class ChannelJoiner:
    def __init__(self):
        self.join_attempts = {}  # {channel_id: last_attempt_time}
        self.join_cooldown = 300  # 5 minutos entre intentos
        self.max_retries = 3

    async def join_channel(self, client: Client, channel_identifier: str) -> Tuple[bool, str, Optional[Chat]]:
        """
        Intenta unirse a un canal con manejo avanzado de errores y reintentos.
        """
        try:
            # Validar el formato del enlace/identificador
            if not self._validate_channel_identifier(channel_identifier):
                return False, "❌ Formato de enlace inválido", None

            # Verificar cooldown
            if not self._check_join_cooldown(channel_identifier):
                return False, "⏳ Debes esperar 5 minutos entre intentos de unión al mismo canal", None

            # Intentar obtener info del chat primero
            try:
                chat = await client.get_chat(channel_identifier)
            except Exception as e:
                logger.warning(f"Error al obtener info del chat {channel_identifier}: {e}")
                chat = None

            if chat:
                # Verificar si ya somos miembros
                try:
                    member = await client.get_chat_member(chat.id, "me")
                    if member and not member.status.name == "LEFT":
                        return True, "✅ Ya eres miembro del canal", chat
                except Exception:
                    pass  # Si hay error, intentaremos unirse de todos modos

            # Intentar unirse con reintentos
            for attempt in range(self.max_retries):
                try:
                    if not chat:
                        chat = await client.join_chat(channel_identifier)
                    else:
                        await client.join_chat(chat.id)
                    
                    self._update_join_attempt(channel_identifier)
                    return True, "✅ Unido exitosamente al canal", chat

                except FloodWait as e:
                    if attempt == self.max_retries - 1:
                        return False, f"⚠️ Rate limit de Telegram. Intenta de nuevo en {e.value} segundos", None
                    await asyncio.sleep(e.value)
                
                except UserAlreadyParticipant:
                    return True, "✅ Ya eres miembro del canal", chat
                
                except InviteHashExpired:
                    return False, "❌ El enlace de invitación ha expirado", None
                
                except InviteHashInvalid:
                    return False, "❌ El enlace de invitación no es válido", None
                
                except ChannelPrivate:
                    return False, "❌ Este canal es privado y requiere invitación", None
                
                except Exception as e:
                    logger.error(f"Error al unirse al canal {channel_identifier}: {e}")
                    if attempt == self.max_retries - 1:
                        return False, f"❌ Error al unirse al canal: {str(e)}", None
                    await asyncio.sleep(2 ** attempt)  # Backoff exponencial

            return False, "❌ No se pudo unir al canal después de varios intentos", None

        except Exception as e:
            logger.error(f"Error general al procesar unión a canal {channel_identifier}: {e}")
            return False, f"❌ Error inesperado: {str(e)}", None

    def _validate_channel_identifier(self, identifier: str) -> bool:
        """Valida el formato del identificador del canal."""
        if isinstance(identifier, int):
            return True
        
        valid_patterns = [
            r'^@[\w\d_]+$',  # @username
            r'^-?\d+$',  # Chat ID
            r'^https?://t\.me/\+[\w\d_-]+$',  # Enlace de invitación privado
            r'^https?://t\.me/joinchat/[\w\d_-]+$',  # Enlace joinchat
            r'^https?://t\.me/[\w\d_]+$',  # Canal público
        ]
        
        return any(re.match(pattern, identifier) for pattern in valid_patterns)

    def _check_join_cooldown(self, channel_identifier: str) -> bool:
        """Verifica si se puede intentar unirse al canal (cooldown)."""
        now = datetime.now()
        if channel_identifier in self.join_attempts:
            if now - self.join_attempts[channel_identifier] < timedelta(seconds=self.join_cooldown):
                return False
        return True

    def _update_join_attempt(self, channel_identifier: str):
        """Actualiza el tiempo del último intento de unión."""
        self.join_attempts[channel_identifier] = datetime.now()

# Instancia global
channel_joiner = ChannelJoiner()