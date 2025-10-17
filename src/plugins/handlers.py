import logging
import re
import os
import asyncio
import time
from datetime import datetime, timezone
from typing import Optional, Union, Tuple, Dict, Any

from pyrogram import Client, filters, StopPropagation
from pyrogram.types import Message, CallbackQuery, Chat, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from pyrogram.errors import (
    PeerIdInvalid, UsernameNotOccupied, ChannelPrivate, 
    InviteHashExpired, InviteRequestSent, UserAlreadyParticipant,
    FloodWait, MessageNotModified
)
from bson.objectid import ObjectId
from tqdm import tqdm

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import (
    build_confirmation_keyboard, build_profiles_management_keyboard,
    build_join_selection_keyboard, build_zip_selection_keyboard,
    build_batch_profiles_keyboard, build_profiles_keyboard,
    build_detailed_format_menu, build_search_results_keyboard
)
from src.helpers.utils import (
    get_greeting, escape_html, sanitize_filename,
    format_time, format_task_details_rich
)
from src.core import downloader
from src.core.exceptions import AuthenticationError, NetworkError
from . import processing_handler

logger = logging.getLogger(__name__)

# Expresión regular para detectar enlaces de Telegram
TELEGRAM_URL_REGEX = r'(https?://)?t\.me/([^\s/]+)(?:/(\d+))?|(?:https?://)?t\.me/c/(\d+)(?:/(\d+))?|(?:https?://)?t\.me/\+([a-zA-Z0-9_-]+)'
# Expresión regular general para URLs
URL_REGEX = r'(https?://[^\s]+)'

try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
except (TypeError, ValueError):
    ADMIN_USER_ID = 0

# Definir las variables globales dentro de las funciones para asegurar su acceso
base_download_dir = "downloads"
os.makedirs(base_download_dir, exist_ok=True)

# --- Funciones de utilidad para manejo de enlaces ---

async def force_dialog_sync(client: Client, chat_id: int, status_msg: Message = None) -> bool:
    """
    Fuerza una sincronización de diálogos para resolver problemas de caché de PeerIdInvalid.
    
    Args:
        client: Cliente de Pyrogram
        chat_id: ID del chat a buscar
        status_msg: Mensaje opcional para actualizar el progreso
        
    Returns:
        bool: True si se encontró el chat en los diálogos, False en caso contrario
    """
    try:
        # Primer intento: sincronización rápida
        if status_msg:
            await status_msg.edit(
                "🔄 <b>Sincronizando caché...</b>\n"
                "Buscando el canal en los diálogos recientes...",
                parse_mode=ParseMode.HTML
            )
        
        async for dialog in client.get_dialogs(limit=50):
            if dialog.chat.id == chat_id:
                logger.info(f"Canal {chat_id} encontrado en sincronización rápida")
                return True
                
        # Segundo intento: sincronización profunda
        if status_msg:
            await status_msg.edit(
                "🔄 <b>Realizando sincronización profunda...</b>\n"
                "Esto puede tomar un momento...",
                parse_mode=ParseMode.HTML
            )
            
        dialog_count = 0
        async for dialog in client.get_dialogs():
            dialog_count += 1
            if dialog.chat.id == chat_id:
                logger.info(f"Canal {chat_id} encontrado en sincronización profunda")
                return True
                
            if status_msg and dialog_count % 50 == 0:
                await status_msg.edit(
                    f"🔄 <b>Sincronización en progreso...</b>\n"
                    f"Chats procesados: {dialog_count}",
                    parse_mode=ParseMode.HTML
                )
                
        logger.warning(f"Canal {chat_id} no encontrado después de sincronización completa")
        return False
        
    except Exception as e:
        logger.error(f"Error durante la sincronización de diálogos: {e}")
        if status_msg:
            await status_msg.edit(
                "❌ <b>Error durante la sincronización</b>\n"
                f"Detalles: {escape_html(str(e))}",
                parse_mode=ParseMode.HTML
            )
        return False

def normalize_chat_id(chat_id: Union[str, int]) -> int:
    """
    Normaliza un ID de chat al formato correcto de Telegram (-100...).
    
    Args:
        chat_id: ID del chat a normalizar (string o int)
        
    Returns:
        int: ID del chat normalizado con el formato -100...
        
    Esta función maneja varios casos:
    1. IDs que ya tienen el prefijo -100
    2. IDs positivos que necesitan el prefijo -100
    3. IDs negativos que necesitan convertirse al formato correcto
    """
    try:
        # Convertir a string para manipulación uniforme
        chat_id_str = str(chat_id)
        
        # Remover cualquier espacio en blanco
        chat_id_str = chat_id_str.strip()
        
        # Caso 1: Ya tiene el formato correcto -100...
        if chat_id_str.startswith('-100'):
            return int(chat_id_str)
            
        # Caso 2: Es un número positivo (necesita -100)
        if chat_id_str.isdigit():
            return int(f'-100{chat_id_str}')
            
        # Caso 3: Es un número negativo
        if chat_id_str.startswith('-'):
            # Remover el signo negativo y agregar -100
            return int(f'-100{chat_id_str[1:]}')
            
        # Si llegamos aquí, intentar convertir directamente
        numeric_id = int(chat_id_str)
        if numeric_id > 0:
            return int(f'-100{numeric_id}')
        else:
            return int(f'-100{abs(numeric_id)}')
            
    except Exception as e:
        error_msg = f"Error normalizando chat_id {chat_id}: {e}"
        logger.error(error_msg)
        raise ValueError(error_msg)

def parse_telegram_url(url: str) -> Dict[str, Any]:
    """
    Analiza una URL de Telegram y extrae información relevante.
    
    Args:
        url: El enlace de Telegram a analizar
        
    Returns:
        Un diccionario con información del enlace:
        - type: 'public_channel', 'private_channel', 'invitation', 'unknown'
        - chat_id: ID del chat (normalizado si es posible)
        - message_id: ID del mensaje (si existe)
        - invite_hash: Hash de invitación (para enlaces de tipo invitation)
        - username: Nombre de usuario (para canales públicos)
        - raw_chat_id: ID del chat sin normalizar (para depuración)
    """
    result = {
        "type": "unknown",
        "chat_id": None,
        "message_id": None,
        "invite_hash": None,
        "username": None,
        "raw_chat_id": None,
        "original_url": url
    }
    
    # Verificar si es un enlace interno de canal privado (t.me/c/123456/789)
    match = re.search(r't\.me/c/(\d+)(?:/(\d+))?', url)
    if match:
        raw_chat_id = match.group(1)
        message_id = match.group(2)
        
        result.update({
            "type": "private_channel",
            "raw_chat_id": raw_chat_id,
            "message_id": int(message_id) if message_id else None
        })
        
        try:
            result["chat_id"] = normalize_chat_id(raw_chat_id)
        except ValueError:
            pass
            
        return result
    
    # Verificar si es un enlace de invitación (t.me/+ABC123)
    match = re.search(r't\.me/\+([a-zA-Z0-9_-]+)', url)
    if match:
        invite_hash = match.group(1)
        result.update({
            "type": "invitation",
            "invite_hash": invite_hash
        })
        return result
    
    # Verificar si es un enlace a un canal público (t.me/username/123)
    match = re.search(r't\.me/([^\s/]+)(?:/(\d+))?', url)
    if match:
        username = match.group(1)
        message_id = match.group(2)
        
        # No incluir joinchat o + como nombre de usuario
        if username in ['joinchat', '+']:
            return result
            
        result.update({
            "type": "public_channel",
            "username": username,
            "message_id": int(message_id) if message_id else None
        })
        return result
    
    return result

def format_size(size: int) -> str:
    """Formatea el tamaño en bytes a una forma legible."""
    if size <= 0:
        return "0 B"
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

# --- Funciones para manejo de progress bar ---

class ProgressTracker:
    """Clase para rastrear y mostrar el progreso de descargas y subidas."""
    
    def __init__(self):
        self.last_update_time = {}
        self.start_times = {}
        self.user_info = {}
        
    def reset(self, operation_id: str):
        """Reinicia los tiempos para una operación específica."""
        self.start_times[operation_id] = asyncio.get_event_loop().time()
        self.last_update_time[operation_id] = 0
    
    def get_elapsed(self, operation_id: str) -> float:
        """Obtiene el tiempo transcurrido desde el inicio de la operación."""
        if operation_id not in self.start_times:
            self.reset(operation_id)
        return asyncio.get_event_loop().time() - self.start_times[operation_id]

# Instancia global para rastrear progreso
progress_tracker = ProgressTracker()

async def show_progress(
    current: int, 
    total: int, 
    status_msg: Message, 
    action: str, 
    operation_id: str, 
    user_client=None
):
    """
    Muestra una barra de progreso detallada con estadísticas.
    
    Args:
        current: Bytes procesados actualmente
        total: Total de bytes a procesar
        status_msg: Mensaje donde mostrar el progreso
        action: Descripción de la acción (ej: "Descargando")
        operation_id: Identificador único de la operación
        user_client: Cliente de Pyrogram (opcional)
    """
    if total <= 0:
        return
    
    try:
        # Evitar actualizaciones demasiado frecuentes (cada 0.5 segundos)
        now = asyncio.get_event_loop().time()
        if operation_id in progress_tracker.last_update_time:
            if now - progress_tracker.last_update_time[operation_id] < 0.5:
                return
        progress_tracker.last_update_time[operation_id] = now
        
        if operation_id not in progress_tracker.start_times:
            progress_tracker.reset(operation_id)
            
        elapsed_time = progress_tracker.get_elapsed(operation_id)
        if elapsed_time <= 0:
            return
            
        percent = current * 100 / total
        done = int(percent / 7.7)  # 13 bloques en total
        pending = 13 - done
        
        speed = current / elapsed_time
        eta = (total - current) / speed if speed > 0 else 0
        
        # Obtener información del userbot si está disponible
        me = None
        if user_client:
            try:
                if operation_id in progress_tracker.user_info:
                    me = progress_tracker.user_info[operation_id]
                else:
                    me = await user_client.get_me()
                    progress_tracker.user_info[operation_id] = me
            except Exception as e:
                logger.debug(f"Error obteniendo info de usuario: {e}")
        
        # Construir barra de progreso
        status_tag = "#TelegramDownload" if "Descarg" in action else "#TelegramUpload"
        progress_bar = (
            f"{action}\n"
            f"[{'▤' * done}{'□' * pending}] {percent:.2f}%\n"
            f"┠ Procesado: {format_size(current)} de {format_size(total)}\n"
            f"┠ Archivo: 1/1\n"
            f"┠ Estado: {status_tag}\n"
            f"┠ ETA: {format_time(eta)}\n"
            f"┠ Velocidad: {format_size(speed)}/s\n"
            f"┠ Tiempo: {format_time(elapsed_time)}\n"
        )
        
        if me:
            progress_bar += f"┠ Motor: {me.first_name}\n"
            progress_bar += f"┖ ID: {me.id}"
        else:
            progress_bar += f"┖ Progreso en curso..."
        
        await status_msg.edit(progress_bar)
    except MessageNotModified:
        pass
    except Exception as e:
        logger.debug(f"Error mostrando progreso: {e}")

async def get_media_info(message: Message) -> dict:
    """
    Obtiene información detallada de un mensaje con archivo multimedia.
    
    Args:
        message: El mensaje de Telegram con multimedia
        
    Returns:
        Un diccionario con información del archivo
    """
    info = {
        "file_name": "",
        "mime_type": "",
        "file_size": 0,
        "duration": 0,
        "width": 0,
        "height": 0,
        "type": "unknown"
    }
    
    if not message:
        return info
        
    if message.video:
        media = message.video
        info["type"] = "video"
        info["duration"] = getattr(media, "duration", 0)
        info["width"] = getattr(media, "width", 0)
        info["height"] = getattr(media, "height", 0)
    elif message.document:
        media = message.document
        info["type"] = "document"
    elif message.audio:
        media = message.audio
        info["type"] = "audio"
        info["duration"] = getattr(media, "duration", 0)
    elif message.photo:
        media = message.photo[-1] if isinstance(message.photo, list) else message.photo
        info["type"] = "photo"
        info["width"] = getattr(media, "width", 0)
        info["height"] = getattr(media, "height", 0)
    elif message.animation:
        media = message.animation
        info["type"] = "animation"
        info["duration"] = getattr(media, "duration", 0)
        info["width"] = getattr(media, "width", 0)
        info["height"] = getattr(media, "height", 0)
    else:
        return info

    # Extraer atributos comunes
    info["file_name"] = getattr(media, "file_name", f"{info['type']}_{int(time.time())}")
    info["mime_type"] = getattr(media, "mime_type", "")
    info["file_size"] = getattr(media, "file_size", 0)
    
    return info

# --- Función principal centralizada para manejar enlaces de Telegram ---

async def handle_telegram_link(client: Client, message: Message, url: str = None) -> None:
    """
    Maneja un enlace de Telegram de forma centralizada siguiendo un flujo lógico y resiliente.
    
    Esta función implementa la lógica completa para procesar cualquier tipo de enlace de Telegram:
    1. Analiza el tipo de enlace
    2. Verifica el acceso con el userbot
    3. Une al userbot al canal si es necesario
    4. Descarga y reenvía el contenido multimedia
    
    Args:
        client: Cliente de Pyrogram del bot
        message: Mensaje original del usuario
        url: URL a procesar (si no se proporciona, se extrae del mensaje)
    """
    user_id = message.from_user.id
    
    # Si no se proporciona URL, extraerla del texto del mensaje
    if not url:
        url = message.text.strip()
    
    # Validar que sea un enlace de Telegram
    if "t.me/" not in url:
        await message.reply("❌ Por favor, envía un enlace válido de Telegram.")
        return
        
    # Enviar mensaje de estado inicial
    status_msg = await message.reply(
        "🔄 <b>Procesando enlace...</b>\n"
        "Por favor, espere mientras verifico el acceso.",
        parse_mode=ParseMode.HTML
    )
    
    try:
        # PASO 1: Analizar el enlace y extraer información
        parsed_url = parse_telegram_url(url)
        logger.info(f"Parsed Telegram URL: {parsed_url}")
        
        # Inicializar cliente de usuario (userbot)
        user_client = getattr(client, 'user_client', None)
        if not user_client:
            await status_msg.edit(
                "❌ <b>Error de configuración:</b> No se ha configurado el cliente de usuario (userbot).",
                parse_mode=ParseMode.HTML
            )
            return
            
        # PASO 2: Verificar acceso según el tipo de enlace
        if parsed_url["type"] == "invitation":
            # 2.A: Es un enlace de invitación
            await status_msg.edit("🔄 Intentando unirse al canal con el enlace de invitación...")
            
            try:
                chat = await user_client.join_chat(url)
                
                # Forzar sincronización inmediata después de unirse
                await status_msg.edit(
                    "🔄 <b>Unido al canal. Sincronizando caché...</b>\n"
                    "Esto puede tomar unos segundos...",
                    parse_mode=ParseMode.HTML
                )
                
                # Forzar actualización de diálogos
                dialog_count = 0
                async for dialog in user_client.get_dialogs(limit=100):
                    dialog_count += 1
                    if dialog.chat.id == chat.id:
                        break
                        
                # Esperar un momento para que Telegram procese la unión
                await asyncio.sleep(2)
                
                # Intentar obtener el chat nuevamente para asegurar la sincronización
                try:
                    chat = await user_client.get_chat(chat.id)
                except Exception as e:
                    logger.warning(f"Error en segunda verificación del chat: {e}")
                
                await status_msg.edit(
                    f"✅ <b>¡Unido exitosamente al canal!</b>\n\n"
                    f"Nombre: <b>{escape_html(chat.title)}</b>\n\n"
                    f"📤 Ahora envía el enlace del mensaje específico que quieres descargar.\n"
                    f"Ejemplo: <code>https://t.me/c/{chat.id}/123</code>",
                    parse_mode=ParseMode.HTML
                )
                
                # Registrar el canal en la base de datos (opcional)
                try:
                    await db_instance.add_monitored_channel(user_id, str(chat.id))
                except Exception as db_error:
                    logger.error(f"Error registrando canal en DB: {db_error}")
                    
            except InviteHashExpired:
                await status_msg.edit(
                    "❌ <b>El enlace de invitación ha expirado.</b>\n\n"
                    "Por favor, solicita un nuevo enlace de invitación.",
                    parse_mode=ParseMode.HTML
                )
                return
            except InviteRequestSent:
                await status_msg.edit(
                    "📩 <b>Se ha enviado una solicitud para unirse al canal.</b>\n\n"
                    "Por favor, espera a que sea aceptada por los administradores del canal.",
                    parse_mode=ParseMode.HTML
                )
                return
            except UserAlreadyParticipant:
                await status_msg.edit(
                    "ℹ️ <b>Ya eres miembro de este canal.</b>\n\n"
                    "Por favor, envía el enlace del mensaje específico que quieres descargar.",
                    parse_mode=ParseMode.HTML
                )
                return
            except FloodWait as e:
                await status_msg.edit(
                    f"⏳ <b>Telegram ha impuesto un límite de tiempo.</b>\n\n"
                    f"Por favor, espera {e.value} segundos antes de intentarlo nuevamente.",
                    parse_mode=ParseMode.HTML
                )
                return
            except Exception as e:
                logger.error(f"Error al unirse al canal: {e}")
                await status_msg.edit(
                    f"❌ <b>Error al unirse al canal:</b> {escape_html(str(e))}\n\n"
                    f"Por favor, verifica que el enlace sea válido y que el userbot tenga permisos para unirse.",
                    parse_mode=ParseMode.HTML
                )
                return
        
        elif parsed_url["type"] == "public_channel":
            # 2.B: Es un enlace a un canal público
            username = parsed_url["username"]
            message_id = parsed_url["message_id"]
            
            await status_msg.edit(f"🔄 Verificando acceso al canal <b>@{escape_html(username)}</b>...")
            
            try:
                # Intentar obtener información del chat
                chat = await user_client.get_chat(username)
                
                # Verificar si somos miembros
                try:
                    member = await user_client.get_chat_member(chat.id, "me")
                    is_member = True
                except Exception:
                    is_member = False
                    
                # Si no somos miembros, intentar unirse
                if not is_member:
                    try:
                        await user_client.join_chat(username)
                        await status_msg.edit(
                            f"✅ <b>¡Unido exitosamente al canal @{escape_html(username)}!</b>",
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as join_error:
                        logger.error(f"Error al unirse al canal @{username}: {join_error}")
                        await status_msg.edit(
                            f"❌ <b>No se pudo unir al canal @{escape_html(username)}:</b>\n"
                            f"{escape_html(str(join_error))}\n\n"
                            f"Es posible que el canal sea privado o requiera aprobación manual.",
                            parse_mode=ParseMode.HTML
                        )
                        return
                
                # Si tenemos un ID de mensaje, procesar mensaje específico
                if message_id:
                    await status_msg.edit(
                        f"🔄 Accediendo al mensaje {message_id} de <b>@{escape_html(username)}</b>...",
                        parse_mode=ParseMode.HTML
                    )
                    
                    try:
                        target_message = await user_client.get_messages(chat.id, message_id)
                        if not target_message:
                            await status_msg.edit(
                                "❌ <b>No se encontró el mensaje especificado.</b>",
                                parse_mode=ParseMode.HTML
                            )
                            return
                            
                        if not target_message.media:
                            await status_msg.edit(
                                "❌ <b>El mensaje no contiene archivos multimedia.</b>",
                                parse_mode=ParseMode.HTML
                            )
                            return
                            
                        # Procesar y descargar el mensaje
                        await process_media_message(client, message, target_message, status_msg)
                        
                    except Exception as msg_error:
                        logger.error(f"Error accediendo al mensaje {message_id}: {msg_error}")
                        await status_msg.edit(
                            f"❌ <b>Error al acceder al mensaje:</b> {escape_html(str(msg_error))}",
                            parse_mode=ParseMode.HTML
                        )
                        return
                else:
                    # No hay ID de mensaje, solo informar sobre el acceso al canal
                    await status_msg.edit(
                        f"✅ <b>Acceso verificado al canal @{escape_html(username)}</b>\n\n"
                        f"📤 Ahora envía el enlace del mensaje específico que quieres descargar.\n"
                        f"Ejemplo: <code>https://t.me/{username}/123</code>",
                        parse_mode=ParseMode.HTML
                    )
            
            except UsernameNotOccupied:
                await status_msg.edit(
                    f"❌ <b>El nombre de usuario @{escape_html(username)} no existe.</b>",
                    parse_mode=ParseMode.HTML
                )
                return
            except PeerIdInvalid:
                await status_msg.edit(
                    f"❌ <b>No se pudo acceder al canal @{escape_html(username)}.</b>\n\n"
                    f"El canal podría ser privado. Necesito un enlace de invitación (t.me/+...).",
                    parse_mode=ParseMode.HTML
                )
                return
            except Exception as e:
                logger.error(f"Error accediendo al canal público {username}: {e}")
                await status_msg.edit(
                    f"❌ <b>Error al acceder al canal:</b> {escape_html(str(e))}",
                    parse_mode=ParseMode.HTML
                )
                return
        
        elif parsed_url["type"] == "private_channel":
            # 2.C: Es un enlace a un canal privado
            chat_id = parsed_url["chat_id"]
            message_id = parsed_url["message_id"]
            raw_chat_id = parsed_url["raw_chat_id"]
            
            # Guardar referencia al mensaje original para uso en callbacks
            original_message = message
            
            if not chat_id:
                await status_msg.edit(
                    "❌ <b>No se pudo procesar el ID del canal.</b>\n\n"
                    "Formato esperado: https://t.me/c/ID_CANAL/ID_MENSAJE",
                    parse_mode=ParseMode.HTML
                )
                return
                
            await status_msg.edit(f"🔄 Verificando acceso al canal privado...")
            
            try:
                # Intentar acceder al chat con el userbot
                chat = await user_client.get_chat(chat_id)
                
                # Si llegamos aquí, tenemos acceso al canal
                if message_id:
                    await status_msg.edit(f"🔄 Accediendo al mensaje {message_id}...")
                    
                    try:
                        target_message = await user_client.get_messages(chat.id, message_id)
                        if not target_message:
                            await status_msg.edit(
                                "❌ <b>No se encontró el mensaje especificado.</b>",
                                parse_mode=ParseMode.HTML
                            )
                            return
                            
                        if not target_message.media:
                            await status_msg.edit(
                                "❌ <b>El mensaje no contiene archivos multimedia.</b>",
                                parse_mode=ParseMode.HTML
                            )
                            return
                            
                        # Procesar y descargar el mensaje
                        await process_media_message(client, message, target_message, status_msg)
                        
                    except Exception as msg_error:
                        logger.error(f"Error accediendo al mensaje {message_id}: {msg_error}")
                        await status_msg.edit(
                            f"❌ <b>Error al acceder al mensaje:</b> {escape_html(str(msg_error))}",
                            parse_mode=ParseMode.HTML
                        )
                        return
                else:
                    # No hay ID de mensaje, solo informar sobre el acceso al canal
                    await status_msg.edit(
                        f"✅ <b>Acceso verificado al canal privado</b>\n\n"
                        f"Nombre: <b>{escape_html(chat.title)}</b>\n\n"
                        f"📤 Ahora envía el enlace del mensaje específico que quieres descargar.\n"
                        f"Ejemplo: <code>https://t.me/c/{raw_chat_id}/123</code>",
                        parse_mode=ParseMode.HTML
                    )
            
            except PeerIdInvalid:
                # Implementar múltiples intentos de recuperación
                logger.warning(f"PeerIdInvalid inicial para {chat_id}. Iniciando proceso de recuperación...")
                
                await status_msg.edit(
                    "🔄 <b>Verificando acceso al canal...</b>\n"
                    "Iniciando proceso de sincronización...",
                    parse_mode=ParseMode.HTML
                )
                
                try:
                    # Primer intento: Esperar y reintentar directamente
                    await asyncio.sleep(2)
                    try:
                        chat = await user_client.get_chat(chat_id)
                        logger.info(f"Acceso recuperado al canal {chat_id} después de una breve espera")
                        found_in_dialogs = True
                    except PeerIdInvalid:
                        # Segundo intento: Forzar sincronización de diálogos
                        found_in_dialogs = await force_dialog_sync(user_client, chat_id, status_msg)
                        
                        if not found_in_dialogs:
                            # Tercer intento: Forzar recarga de sesión
                            await status_msg.edit(
                                "🔄 <b>Intentando reconexión...</b>\n"
                                "Por favor, espera un momento...",
                                parse_mode=ParseMode.HTML
                            )
                            
                            try:
                                await status_msg.edit(
                                    "🔄 <b>Reiniciando sesión...</b>\n"
                                    "Esto puede tomar unos momentos.",
                                    parse_mode=ParseMode.HTML
                                )
                                
                                # Forzar reconexión del cliente
                                try:
                                    await user_client.disconnect()
                                except Exception:
                                    pass
                                    
                                await asyncio.sleep(2)
                                
                                # Intentar reconectar varias veces si es necesario
                                for attempt in range(3):
                                    try:
                                        if not user_client.is_connected:
                                            await user_client.connect()
                                        
                                        # Verificar que realmente estamos conectados
                                        me = await user_client.get_me()
                                        if me:
                                            logger.info(f"Reconexión exitosa después del intento {attempt + 1}")
                                            break
                                    except Exception as conn_error:
                                        logger.warning(f"Intento de reconexión {attempt + 1} falló: {conn_error}")
                                        await asyncio.sleep(2)
                                
                                await status_msg.edit(
                                    "🔄 <b>Verificando acceso...</b>\n"
                                    "Realizando comprobación final.",
                                    parse_mode=ParseMode.HTML
                                )
                                
                                # Esperar a que la sesión se estabilice
                                await asyncio.sleep(2)
                                
                                # Intentar sincronización una última vez
                                found_in_dialogs = await force_dialog_sync(user_client, chat_id, status_msg)
                                
                                if not found_in_dialogs:
                                    # Último intento: acceso directo
                                    try:
                                        chat = await user_client.get_chat(chat_id)
                                        if chat:
                                            found_in_dialogs = True
                                            logger.info(f"Acceso recuperado a {chat_id} después de reconexión")
                                    except Exception as final_error:
                                        logger.error(f"Error en intento final de acceso: {final_error}")
                                        
                            except Exception as e:
                                logger.error(f"Error durante la reconexión: {e}")
                                found_in_dialogs = False
                                await status_msg.edit(
                                    "❌ <b>Error de conexión</b>\n"
                                    "No se pudo restablecer la sesión.\n"
                                    "Por favor, intenta nuevamente en unos momentos.",
                                    parse_mode=ParseMode.HTML
                                )
                            
                        # Actualizar mensaje de estado cada 20 diálogos
                        if dialog_count % 20 == 0:
                            await status_msg.edit(
                                f"🔄 <b>Sincronizando caché de sesión...</b>\n\n"
                                f"Chats procesados: {dialog_count}\n"
                                f"Buscando acceso al canal...",
                                parse_mode=ParseMode.HTML
                            )
                    
                    if found_in_dialogs:
                        await status_msg.edit(
                            "✅ <b>¡Canal encontrado!</b>\n\n"
                            "Intentando acceder nuevamente...",
                            parse_mode=ParseMode.HTML
                        )
                        
                        # Intentar acceder al chat nuevamente después del refresco
                        try:
                            chat = await user_client.get_chat(chat_id)
                            
                            if message_id:
                                await status_msg.edit(f"🔄 Accediendo al mensaje {message_id}...")
                                target_message = await user_client.get_messages(chat.id, message_id)
                                
                                if not target_message:
                                    await status_msg.edit(
                                        "❌ <b>No se encontró el mensaje especificado.</b>",
                                        parse_mode=ParseMode.HTML
                                    )
                                    return
                                    
                                if not target_message.media:
                                    await status_msg.edit(
                                        "❌ <b>El mensaje no contiene archivos multimedia.</b>",
                                        parse_mode=ParseMode.HTML
                                    )
                                    return
                                    
                                # Procesar y descargar el mensaje
                                await process_media_message(client, original_message, target_message, status_msg)
                            else:
                                await status_msg.edit(
                                    f"✅ <b>Acceso verificado al canal privado</b>\n\n"
                                    f"Nombre: <b>{escape_html(chat.title)}</b>\n\n"
                                    f"📤 Ahora envía el enlace del mensaje específico que quieres descargar.\n"
                                    f"Ejemplo: <code>https://t.me/c/{raw_chat_id}/123</code>",
                                    parse_mode=ParseMode.HTML
                                )
                                
                        except PeerIdInvalid:
                            logger.error(f"PeerIdInvalid persistente para {chat_id} incluso después del refresco")
                            await status_msg.edit(
                                "❌ <b>Error persistente de acceso</b>\n\n"
                                "A pesar de encontrar el canal en mis diálogos, no puedo acceder.\n"
                                "Esto puede indicar un problema con los permisos o la sesión.\n\n"
                                "Por favor, intenta:\n"
                                "1. Enviar un nuevo enlace de invitación (t.me/+...)\n"
                                "2. Verificar que el userbot siga siendo miembro del canal",
                                parse_mode=ParseMode.HTML
                            )
                            return
                    else:
                        await status_msg.edit(
                            "❌ <b>No tengo acceso a este canal privado.</b>\n\n"
                            "No encontré el canal en mi lista de diálogos.\n"
                            "Posibles soluciones:\n"
                            "1. Envía un enlace de invitación (t.me/+...)\n"
                            "2. Asegúrate de que el userbot sea miembro del canal",
                            parse_mode=ParseMode.HTML
                        )
                        return
                        
                except Exception as refresh_error:
                    logger.error(f"Error durante el refresco de diálogos: {refresh_error}")
                    await status_msg.edit(
                        "❌ <b>Error durante la sincronización</b>\n\n"
                        f"No se pudo completar el proceso: {escape_html(str(refresh_error))}\n\n"
                        "Por favor, intenta nuevamente o proporciona un enlace de invitación.",
                        parse_mode=ParseMode.HTML
                    )
                    return
            except Exception as e:
                logger.error(f"Error accediendo al canal privado {chat_id}: {e}")
                await status_msg.edit(
                    f"❌ <b>Error al acceder al canal:</b> {escape_html(str(e))}",
                    parse_mode=ParseMode.HTML
                )
                return
        
        else:
            # Tipo de enlace desconocido o no soportado
            await status_msg.edit(
                "❌ <b>Formato de enlace no reconocido.</b>\n\n"
                "Formatos válidos:\n"
                "• Canal privado: https://t.me/+abc123...\n"
                "• Canal público: https://t.me/nombre_canal\n"
                "• Mensaje específico: https://t.me/nombre_canal/123 o https://t.me/c/ID/123",
                parse_mode=ParseMode.HTML
            )
            return
    
    except Exception as e:
        logger.error(f"Error procesando enlace {url}: {e}", exc_info=True)
        await status_msg.edit(
            f"❌ <b>Error inesperado al procesar el enlace:</b>\n"
            f"{escape_html(str(e))}",
            parse_mode=ParseMode.HTML
        )

async def process_media_message(client: Client, original_message: Message, target_message: Message, status_msg: Message):
    """
    Procesa un mensaje con contenido multimedia: lo descarga y lo reenvía al usuario.
    
    Args:
        client: Cliente de Pyrogram del bot
        original_message: Mensaje original del usuario
        target_message: Mensaje de Telegram con el contenido multimedia a descargar
        status_msg: Mensaje de estado donde mostrar el progreso
    """
    try:
        # Usar el userbot para las operaciones
        user_client = client.user_client
        operation_id = f"media_{original_message.id}"
        progress_tracker.reset(operation_id)
        
        # Obtener información del archivo
        media_info = await get_media_info(target_message)
        
        # Asegurar que tenemos un nombre de archivo válido
        if not media_info['file_name']:
            media_info['file_name'] = f"{media_info['type']}_{int(time.time())}"
        
        # Mostrar mensaje inicial con información detallada
        initial_message = (
            f"📥 <b>Preparando Descarga</b>\n\n"
            f"📁 <b>Archivo:</b> {escape_html(media_info['file_name'])}\n"
            f"📊 <b>Tamaño:</b> {format_size(media_info['file_size'])}\n"
            f"📱 <b>Tipo:</b> {media_info['type'].upper()}\n"
        )
        
        if media_info['type'] == 'video':
            initial_message += f"🎥 <b>Resolución:</b> {media_info['width']}x{media_info['height']}\n"
            initial_message += f"⏱ <b>Duración:</b> {format_time(media_info['duration'])}\n"
        elif media_info['type'] == 'audio':
            initial_message += f"⏱ <b>Duración:</b> {format_time(media_info['duration'])}\n"
        
        initial_message += "\n⏳ Iniciando descarga..."
        
        await status_msg.edit(initial_message, parse_mode=ParseMode.HTML)
        await asyncio.sleep(1)  # Breve pausa para mostrar la info
        
        # Preparar carpeta temporal
        temp_path = os.path.join(os.getcwd(), "downloads")
        os.makedirs(temp_path, exist_ok=True)
        
        # Crear un ID único para el archivo
        unique_id = f"{int(time.time())}_{original_message.from_user.id}"
        safe_filename = sanitize_filename(media_info['file_name'])
        file_path = os.path.join(temp_path, f"{unique_id}_{safe_filename}")
        
        # Descargar el archivo usando el userbot
        download_start_time = asyncio.get_event_loop().time()
        downloaded_path = await user_client.download_media(
            target_message,
            file_name=file_path,
            progress=show_progress,
            progress_args=(status_msg, "Descargando archivo", operation_id, user_client)
        )
        
        if not downloaded_path or not os.path.exists(downloaded_path):
            await status_msg.edit(
                "❌ <b>Error:</b> No se pudo descargar el archivo.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Mostrar resumen de la descarga
        download_time = asyncio.get_event_loop().time() - download_start_time
        download_speed = media_info['file_size'] / download_time if download_time > 0 else 0
        
        await status_msg.edit(
            f"✅ <b>Descarga completada</b>\n\n"
            f"⚡️ <b>Velocidad promedio:</b> {format_size(download_speed)}/s\n"
            f"⏱ <b>Tiempo total:</b> {format_time(download_time)}\n\n"
            f"🔄 Preparando para subir...",
            parse_mode=ParseMode.HTML
        )
        
        # Breve pausa antes de comenzar la subida
        await asyncio.sleep(1)
        
        # Preparar metadatos para la subida
        caption = target_message.caption or f"Archivo procesado por @{original_message.from_user.username or 'Media_Suite_Bot'}"
        thumb_path = None
        
        # Extraer thumbnail para videos si está disponible
        if target_message.video and hasattr(target_message.video, 'thumbs') and target_message.video.thumbs:
            try:
                thumb_path = await user_client.download_media(
                    target_message.video.thumbs[0],
                    file_name=f"{temp_path}/thumb_{unique_id}.jpg"
                )
            except Exception as thumb_error:
                logger.warning(f"Error descargando thumbnail: {thumb_error}")
        
        # Iniciar subida
        upload_start_time = asyncio.get_event_loop().time()
        progress_tracker.reset(operation_id)  # Resetear tiempo para la subida
        
        try:
            # Enviar el archivo según su tipo
            if target_message.video:
                await user_client.send_video(
                    original_message.chat.id,
                    downloaded_path,
                    thumb=thumb_path,
                    duration=media_info['duration'],
                    width=media_info['width'],
                    height=media_info['height'],
                    caption=caption,
                    progress=show_progress,
                    progress_args=(status_msg, "Subiendo video", operation_id, user_client),
                    supports_streaming=True
                )
            elif target_message.document:
                await user_client.send_document(
                    original_message.chat.id,
                    downloaded_path,
                    thumb=thumb_path,
                    caption=caption,
                    progress=show_progress,
                    progress_args=(status_msg, "Subiendo documento", operation_id, user_client)
                )
            elif target_message.audio:
                await user_client.send_audio(
                    original_message.chat.id,
                    downloaded_path,
                    duration=media_info['duration'],
                    caption=caption,
                    progress=show_progress,
                    progress_args=(status_msg, "Subiendo audio", operation_id, user_client)
                )
            elif target_message.photo:
                await user_client.send_photo(
                    original_message.chat.id,
                    downloaded_path,
                    caption=caption
                )
            elif target_message.animation:
                await user_client.send_animation(
                    original_message.chat.id,
                    downloaded_path,
                    caption=caption,
                    progress=show_progress,
                    progress_args=(status_msg, "Subiendo animación", operation_id, user_client)
                )
            else:
                # Tipo de archivo no identificado específicamente, enviar como documento
                await user_client.send_document(
                    original_message.chat.id,
                    downloaded_path,
                    caption=caption,
                    progress=show_progress,
                    progress_args=(status_msg, "Subiendo archivo", operation_id, user_client)
                )
                
            # Mostrar resumen final
            total_time = asyncio.get_event_loop().time() - progress_tracker.start_times[operation_id]
            upload_time = asyncio.get_event_loop().time() - upload_start_time
            upload_speed = media_info['file_size'] / upload_time if upload_time > 0 else 0
            
            me = await user_client.get_me()
            
            await status_msg.edit(
                f"✅ <b>¡Tarea Completada!</b>\n\n"
                f"📁 <b>Archivo:</b> {escape_html(media_info['file_name'])}\n"
                f"📊 <b>Tamaño:</b> {format_size(media_info['file_size'])}\n"
                f"⏱ <b>Tiempo total:</b> {format_time(total_time)}\n"
                f"🚀 <b>Modo:</b> Telegram\n"
                f"👤 <b>Procesado por:</b> {me.first_name}\n"
                f"🆔 <b>ID:</b> {me.id}",
                parse_mode=ParseMode.HTML
            )
            
        except Exception as e:
            logger.error(f"Error al enviar el archivo: {e}")
            await status_msg.edit(
                f"❌ <b>Error al enviar el archivo</b>\n\n"
                f"<code>{escape_html(str(e))}</code>",
                parse_mode=ParseMode.HTML
            )
            
    except Exception as e:
        logger.error(f"Error procesando mensaje multimedia: {e}")
        await status_msg.edit(
            f"❌ <b>Error al procesar el contenido:</b>\n"
            f"<code>{escape_html(str(e))}</code>",
            parse_mode=ParseMode.HTML
        )
    finally:
        # Limpiar archivos temporales
        try:
            if 'downloaded_path' in locals() and os.path.exists(downloaded_path):
                os.remove(downloaded_path)
            if 'thumb_path' in locals() and thumb_path and os.path.exists(thumb_path):
                os.remove(thumb_path)
        except Exception as cleanup_error:
            logger.error(f"Error al limpiar archivos temporales: {cleanup_error}")

# --- Manejadores de Pyrogram ---

@Client.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    """Maneja el comando /start."""
    try:
        # Obtener información del usuario
        user_id = message.from_user.id
        first_name = message.from_user.first_name
        
        # Mensaje de bienvenida personalizado con el nombre del usuario
        welcome_message = (
            f"👋 ¡Hola {first_name}!\n\n"
            "🤖 Soy tu asistente para descargar contenido multimedia de Telegram.\n\n"
            "📱 <b>¿Qué puedo hacer?</b>\n"
            "• Descargar videos de canales privados\n"
            "• Procesar archivos multimedia\n"
            "• Gestionar descargas masivas\n\n"
            "🔑 <b>Comandos principales:</b>\n"
            "• /start - Muestra este mensaje\n"
            "• /get_restricted - Descarga de canales privados\n"
            "• /panel - Accede al panel de control\n\n"
            "📤 También puedes enviarme directamente:\n"
            "• Enlaces de Telegram (t.me/...)\n"
            "• Enlaces de invitación a canales\n"
            "• Videos para procesar\n\n"
            "¡Estoy listo para ayudarte! 🚀"
        )
        
        # Registrar o actualizar usuario en la base de datos primero
        try:
            await db_instance.register_user(user_id)
        except Exception as db_error:
            logger.error(f"Error registrando usuario en DB: {db_error}")
        
        # Crear teclado básico sin parámetros adicionales
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Descargar Video", callback_data="download_video")],
            [InlineKeyboardButton("⚙️ Panel de Control", callback_data="open_panel")],
            [InlineKeyboardButton("ℹ️ Ayuda", callback_data="show_help")]
        ])
        
        # Enviar mensaje de bienvenida con teclado básico
        await message.reply(
            welcome_message,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
            
    except Exception as e:
        logger.error(f"Error en comando start: {e}", exc_info=True)  # Agregado exc_info para más detalles
        
        # Mensaje de error más informativo
        error_message = (
            "❌ <b>Ocurrió un error al iniciar el bot.</b>\n\n"
            "Detalles técnicos:\n"
            f"<code>{escape_html(str(e))}</code>\n\n"
            "Por favor, intenta nuevamente en unos momentos o contacta al administrador."
        )
        
        try:
            await message.reply(
                error_message,
                parse_mode=ParseMode.HTML
            )
        except Exception as reply_error:
            # Si falla incluso el mensaje de error, intentar un mensaje simple
            try:
                await message.reply("Error al iniciar. Por favor, intenta más tarde.")
            except:
                pass  # Si todo falla, al menos tenemos el error en los logs

@Client.on_message(filters.private & filters.video, group=1)
async def handle_direct_video(client: Client, message: Message):
    """Maneja videos enviados directamente al bot."""
    try:
        # Enviar mensaje de estado inicial
        status_msg = await message.reply(
            "🎥 <b>Video recibido</b>\n"
            "Procesando información...",
            parse_mode=ParseMode.HTML
        )
        
        # Obtener información del video
        video_info = await get_media_info(message)
        
        if not video_info["file_name"]:
            video_info["file_name"] = f"video_{int(time.time())}.mp4"
            
        # Mostrar información del video
        info_message = (
            f"📹 <b>Detalles del Video</b>\n\n"
            f"📁 <b>Nombre:</b> {escape_html(video_info['file_name'])}\n"
            f"📊 <b>Tamaño:</b> {format_size(video_info['file_size'])}\n"
            f"🎬 <b>Duración:</b> {format_time(video_info['duration'])}\n"
            f"📺 <b>Resolución:</b> {video_info['width']}x{video_info['height']}\n\n"
            f"⚙️ <b>Estado:</b> Listo para procesar\n"
            f"📋 El video ha sido agregado al panel."
        )
        
        # Construir el mensaje y teclado para el panel
        panel_message = (
            f"{info_message}\n\n"
            f"✨ <b>Acciones disponibles:</b>\n"
            f"• Procesar video\n"
            f"• Cortar/recortar\n"
            f"• Extraer audio"
        )
        
        # Usar el teclado simplificado sin parámetros
        await status_msg.edit(
            panel_message,
            parse_mode=ParseMode.HTML,
            reply_markup=build_detailed_format_menu()  # Ahora acepta None por defecto
        )
        
        # Registrar el video en la base de datos
        try:
            video_data = {
                "user_id": message.from_user.id,
                "file_name": video_info["file_name"],
                "file_size": video_info["file_size"],
                "duration": video_info["duration"],
                "width": video_info["width"],
                "height": video_info["height"],
                "mime_type": video_info["mime_type"],
                "message_id": message.id,
                "status": "pending",
                "created_at": datetime.now(timezone.utc)
            }
            await db_instance.add_pending_video(video_data)
            
        except Exception as db_error:
            logger.error(f"Error registrando video en DB: {db_error}")
            await status_msg.edit(
                f"{info_message}\n\n"
                "⚠️ <b>Advertencia:</b> No se pudo registrar en la base de datos.",
                parse_mode=ParseMode.HTML
            )
            
    except Exception as e:
        logger.error(f"Error procesando video: {e}")
        if 'status_msg' in locals():
            await status_msg.edit(
                "❌ <b>Error al procesar el video</b>\n"
                f"Detalles: {escape_html(str(e))}",
                parse_mode=ParseMode.HTML
            )
        else:
            await message.reply(
                "❌ <b>Error al procesar el video</b>\n"
                "Por favor, intenta nuevamente.",
                parse_mode=ParseMode.HTML
            )

@Client.on_message(filters.private & filters.text & filters.regex(r"^/"), group=-1)
async def state_guardian(client: Client, message: Message):
    """Resetea el estado del usuario si se emite un comando durante una operación."""
    user_id = message.from_user.id
    user_state = await db_instance.get_user_state(user_id)
    
    if user_state.get("status") != "idle":
        logger.warning(
            f"State Guardian: User {user_id} sent command '{message.text}' "
            f"while in state '{user_state.get('status')}'. Resetting state."
        )
        
        if source_id := user_state.get("data", {}).get("source_message_id"):
            try: 
                await client.edit_message_text(user_id, source_id, "✖️ Operación cancelada.")
            except Exception:
                pass
                
        await db_instance.set_user_state(user_id, "idle")
        await message.reply("✔️ Operación anterior cancelada.")

@Client.on_message(filters.command("get_restricted") & filters.private)
async def get_restricted_command(client: Client, message: Message):
    """Inicia el proceso de obtener contenido de un canal restringido."""
    try:
        # Obtener el enlace del mensaje
        text = message.text.split(maxsplit=1)
        
        if len(text) < 2:
            await db_instance.set_user_state(message.from_user.id, "waiting_restricted_link")
            return await message.reply(
                "📥 <b>Descarga de Contenido Restringido</b>\n\n"
                "Por favor, envía el enlace del contenido que deseas descargar.\n\n"
                "<b>Formatos válidos:</b>\n"
                "• Canal privado: https://t.me/+abc123...\n"
                "• Canal público: https://t.me/nombre_canal\n"
                "• Mensaje específico: https://t.me/nombre_canal/123 o https://t.me/c/123456789/123",
                parse_mode=ParseMode.HTML
            )
            
        url = text[1].strip()
        # Llamar a la función centralizada para manejar el enlace
        await handle_telegram_link(client, message, url)
        
    except Exception as e:
        logger.error(f"Error en get_restricted_command: {str(e)}", exc_info=True)
        await message.reply("❌ Ocurrió un error inesperado. Por favor, intenta nuevamente.")

@Client.on_message(filters.text & filters.private)
async def text_message_handler(client: Client, message: Message):
    """Manejador principal para todos los mensajes de texto."""
    try:
        user_id = message.from_user.id
        text = message.text.strip()
        
        # No procesar comandos aquí
        if text.startswith('/'):
            return

        # Verificar si es un enlace de Telegram
        if "t.me/" in text:
            # Usar la función centralizada para manejar enlaces de Telegram
            await handle_telegram_link(client, message)
            return
            
        # Manejar estados especiales
        user_state = await db_instance.get_user_state(user_id)
        
        # Si el usuario está esperando un enlace restringido
        if user_state.get("status") == "waiting_restricted_link":
            await handle_telegram_link(client, message)
            await db_instance.set_user_state(user_id, "idle")
            return
            
        # Si el usuario está esperando un enlace de canal
        elif user_state.get("status") == "waiting_channel_link":
            await handle_telegram_link(client, message)
            await db_instance.set_user_state(user_id, "idle")
            return
            
        # Manejar otros estados si es necesario
        elif user_state.get("status") != "idle":
            await processing_handler.handle_text_input_for_state(client, message, user_state)
            return
            
        # Verificar si es otro tipo de URL (no de Telegram)
        if re.search(URL_REGEX, text):
            await handle_url_input(client, message, text)
            return
            
        # Si no es URL ni un estado especial, asumir búsqueda de música
        await handle_music_search(client, message, text)
            
    except Exception as e:
        logger.error(f"Error en text_message_handler: {str(e)}", exc_info=True)
        await message.reply(
            "❌ <b>Error inesperado al procesar el mensaje.</b>\n"
            "Por favor, intenta nuevamente o contacta al administrador.",
            parse_mode=ParseMode.HTML
        )

# --- Funciones para mantener compatibilidad con el código existente ---

async def handle_url_input(client: Client, message: Message, url: str):
    """Maneja un enlace que no es de Telegram."""
    # Esta función es un placeholder para mantener compatibilidad
    # con la implementación anterior, en caso de que se necesite manejar
    # otros tipos de URLs (YouTube, etc.)
    
    # Si es un enlace de Telegram, redirigir a la función especializada
    if "t.me/" in url:
        return await handle_telegram_link(client, message, url)
        
    # Para otros tipos de URL, implementar lógica según sea necesario
    await message.reply(
        "🔗 <b>Enlace detectado</b>\n\n"
        "Este tipo de enlace no está soportado actualmente.",
        parse_mode=ParseMode.HTML
    )

async def handle_music_search(client: Client, message: Message, query: str):
    """Maneja una búsqueda de música."""
    # Esta función es un placeholder para mantener compatibilidad
    # con la implementación anterior
    
    await message.reply(
        "🎵 <b>Búsqueda de música</b>\n\n"
        "Esta función está en mantenimiento.",
        parse_mode=ParseMode.HTML
    )

# Funciones auxiliares para mantener compatibilidad con código existente

def format_time(seconds: float) -> str:
    """Formatea el tiempo en segundos a una forma legible."""
    if seconds < 0:
        return "0s"
    elif seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    else:
        hours = seconds / 3600
        minutes = (seconds % 3600) / 60
        return f"{hours:.0f}h {minutes:.0f}m"