# --- START OF FILE src/plugins/handlers.py ---

import logging
import re
from datetime import datetime
import asyncio
import os
import time
from typing import Optional, Union, Tuple, Any

from pyrogram import Client, filters, StopPropagation
from pyrogram.types import Message, CallbackQuery, Chat
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified
from bson.objectid import ObjectId

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import (build_confirmation_keyboard, build_profiles_management_keyboard,
                                   build_join_selection_keyboard, build_zip_selection_keyboard,
                                   build_batch_profiles_keyboard, build_profiles_keyboard,
                                   build_detailed_format_menu, build_search_results_keyboard)
from src.helpers.utils import (get_greeting, escape_html, sanitize_filename,
                               format_time, format_task_details_rich)
from src.core import downloader
from src.core.exceptions import AuthenticationError, NetworkError
from . import processing_handler

logger = logging.getLogger(__name__)

URL_REGEX = r'(https?://[^\s]+)'
try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
except (TypeError, ValueError):
    ADMIN_USER_ID = 0

@Client.on_message(filters.private & filters.text & filters.regex(r"^/"), group=-1)
async def state_guardian(client: Client, message: Message):
    """
    Guardián de estado: Resetea el estado del usuario si se emite un COMANDO
    durante una operación que espera una entrada.
    """
    user_id = message.from_user.id
    user_state = await db_instance.get_user_state(user_id)
    
    if user_state.get("status") != "idle":
        logger.warning(
            f"State Guardian: User {user_id} sent command '{message.text}' "
            f"while in state '{user_state.get('status')}'. Resetting state."
        )
        
        if source_id := user_state.get("data", {}).get("source_message_id"):
            try: await client.edit_message_text(user_id, source_id, "✖️ Operación cancelada.")
            except Exception: pass
                
        await db_instance.set_user_state(user_id, "idle")
        
        await message.reply("✔️ Operación anterior cancelada.")


@Client.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    greeting = get_greeting(message.from_user.id)
    start_message = (
        f"¡A sus órdenes, {greeting}! Bienvenido a la <b>Suite de Medios v21.0 (Pro)</b>.\n\n"
        "<b>📋 Comandos Principales:</b>\n"
        "• /panel - Muestra su mesa de trabajo con las tareas pendientes\n"
        "• /p <code>[ID]</code> - Abre el menú de configuración para una tarea\n"
        "• /p clean - Limpia todas las tareas de su panel\n"
        "• /profiles - Gestiona sus perfiles de configuración guardados\n\n"
        "<b>🛠️ Herramientas de Lote:</b>\n"
        "• /join - Une varios videos en un solo archivo\n"
        "• /zip - Comprime varias tareas en un archivo .zip\n"
        "• /p_all - Procesa todas las tareas con un perfil\n\n"
        "<b>🔒 Canales Restringidos:</b>\n"
        "• /add_channel - Registra un canal para monitoreo automático\n"
        "• /list_channels - Muestra tus canales monitoreados\n"
        "• /get_restricted - Descarga contenido enviando un enlace\n"
        "• /monitor <code>[on/off]</code> - Activa/desactiva el monitoreo\n\n"
        "<b>⚙️ Configuración:</b>\n"
        "• /settings - Ajustes generales del bot\n"
        "• /presets - Gestiona perfiles de configuración\n"
        "• /queue - Muestra estado de la cola de tareas\n"
        "• /cancel - Cancela la operación en curso\n\n"
        "<b>👥 Comandos de Admin:</b>\n"
        "• /stats - Muestra estadísticas generales\n"
        "• /user <code>[ID]</code> - Ver detalles de un usuario\n"
        "• /ban <code>[ID] [razón]</code> - Banear usuario\n"
        "• /unban <code>[ID]</code> - Desbanear usuario\n\n"
        "📤 Para empezar, envíe un archivo o enlace para procesar.")
    
    await message.reply(start_message, parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("panel") & filters.private)
async def panel_command(client: Client, message: Message):
    user_id = message.from_user.id
    greeting = get_greeting(user_id)
    pending_tasks = await db_instance.get_pending_tasks(user_id, status_filter="pending_processing")
    
    if not pending_tasks:
        return await message.reply(f"✅ ¡{greeting}, su mesa de trabajo está vacía!")
        
    response = [f"📋 <b>{greeting}, su mesa de trabajo actual:</b>"]
    for i, task in enumerate(pending_tasks):
        response.append(format_task_details_rich(task, i + 1))
    
    response.extend([f"\nUse /p <code>[ID]</code> para configurar una tarea.", f"Use /p clean para limpiar todo el panel."])
    await message.reply("\n".join(response), parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@Client.on_message(filters.command("p") & filters.private)
async def process_command(client: Client, message: Message):
    user_id = message.from_user.id
    parts = message.text.split()
    
    if len(parts) < 2:
        return await message.reply("Uso: `/p [ID]` o `/p clean`.", parse_mode=ParseMode.MARKDOWN)
    
    action = parts[1].lower()
    
    if action == "clean":
        return await message.reply("¿Seguro que desea eliminar TODAS las tareas de su panel?", reply_markup=build_confirmation_keyboard("panel_delete_all_confirm", "panel_delete_all_cancel"))
    
    if not action.isdigit():
        return await message.reply("El ID debe ser un número. Use `/panel` para ver los IDs de sus tareas.")
        
    task_index = int(action) - 1
    pending_tasks = await db_instance.get_pending_tasks(user_id, status_filter="pending_processing")
    
    if 0 <= task_index < len(pending_tasks):
        await processing_handler.open_task_menu_from_p(client, message, str(pending_tasks[task_index]['_id']))
    else:
        await message.reply(f"❌ ID inválido. Tiene {len(pending_tasks)} tareas en el panel.")

@Client.on_message(filters.command("profiles") & filters.private)
async def profiles_command(client: Client, message: Message):
    presets = await db_instance.get_user_presets(message.from_user.id)
    await message.reply("<b>Gestión de Perfiles:</b>\nAquí puede eliminar perfiles de configuración guardados.", reply_markup=build_profiles_management_keyboard(presets), parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("join") & filters.private)
async def join_command(client: Client, message: Message):
    user_id = message.from_user.id
    video_tasks = await db_instance.get_pending_tasks(user_id, file_type_filter="video", status_filter="pending_processing")
    if len(video_tasks) < 2: return await message.reply("❌ Necesita al menos 2 videos en su panel para usar /join.")
    await db_instance.set_user_state(user_id, "selecting_join_files", data={"selected_ids": []})
    await message.reply("🎬 <b>Modo de Unión</b>\nSeleccione los videos que desea unir en el orden correcto:", reply_markup=build_join_selection_keyboard(video_tasks, []), parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("zip") & filters.private)
async def zip_command(client: Client, message: Message):
    user_id = message.from_user.id
    tasks = await db_instance.get_pending_tasks(user_id, status_filter="pending_processing")
    if not tasks: return await message.reply("❌ Su panel está vacío. No hay nada que comprimir.")
    await db_instance.set_user_state(user_id, "selecting_zip_files", data={"selected_ids": []})
    await message.reply("📦 <b>Modo de Compresión</b>\nSeleccione las tareas que desea incluir en el archivo .zip:", reply_markup=build_zip_selection_keyboard(tasks, []), parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("p_all") & filters.private)
async def process_all_command(client: Client, message: Message):
    user_id = message.from_user.id
    tasks = await db_instance.get_pending_tasks(user_id, status_filter="pending_processing")
    if not tasks: return await message.reply("❌ No hay tareas pendientes en su panel para procesar.")
    presets = await db_instance.get_user_presets(user_id)
    await message.reply(f"Va a procesar en lote <b>{len(tasks)}</b> tareas.\nSeleccione un perfil para aplicar a todas.", reply_markup=build_batch_profiles_keyboard(presets), parse_mode=ParseMode.HTML)

@Client.on_message(filters.media & filters.private, group=1)
async def media_gatekeeper(client: Client, message: Message):
    user_id = message.from_user.id
    user_state = await db_instance.get_user_state(user_id)
    if user_state.get("status") != "idle":
        return await processing_handler.handle_media_input_for_state(client, message, user_state)
    media = message.video or message.audio or message.document
    file_type = 'video' if message.video else 'audio' if message.audio else 'document'
    metadata, file_name = {}, getattr(media, 'file_name', f"{file_type}_{datetime.utcnow().timestamp()}")
    if file_type == 'video' and hasattr(media, 'width'):
        metadata = {"resolution": f"{media.width}x{media.height}", "duration": getattr(media, 'duration', 0)}
    elif file_type == 'audio':
        metadata = {"duration": getattr(media, 'duration', 0)}
    metadata['size'] = getattr(media, 'file_size', 0)
    task_id = await db_instance.add_task(user_id=user_id, file_type=file_type, file_name=file_name, file_id=media.file_id, status="pending_processing", metadata=metadata)
    if not task_id: return await message.reply("❌ Error al registrar la tarea en la base de datos.")
    count = await db_instance.tasks.count_documents({'user_id': user_id, 'status': 'pending_processing'})
    status_msg = await message.reply(f"✅ Añadido al panel como tarea <b>#{count}</b>.", parse_mode=ParseMode.HTML)
    if presets := await db_instance.get_user_presets(user_id):
        await status_msg.edit("¿Desea aplicar un perfil de configuración a esta tarea?", reply_markup=build_profiles_keyboard(str(task_id), presets))

# [FIX] Se corrige el decorador para evitar el TypeError.
# La lógica de grupos asegura que este manejador solo se ejecute si un manejador de comandos (group=0 por defecto) no lo ha hecho.
async def get_message_info(client: Client, chat_id: Union[int, str], message_id: int) -> tuple[bool, str, Optional[Message]]:
    """Obtiene información de un mensaje específico"""
    try:
        message = await client.get_messages(chat_id, message_id)
        if not message:
            return False, "❌ No se encontró el mensaje.", None
        if not message.media:
            return False, "❌ El mensaje no contiene archivos multimedia.", None
        return True, "✅ Mensaje encontrado con contenido multimedia.", message
    except Exception as e:
        logger.error(f"Error getting message {message_id} from {chat_id}: {e}")
        return False, f"❌ Error al obtener el mensaje: {str(e)}", None

async def get_chat_info(client: Client, url: str) -> tuple[bool, str, Optional[Union[Chat, Message]], Optional[int]]:
    """Obtiene información del chat o mensaje"""
    try:
        user_client = client.user_client
        parts = url.split('/')
        message_id = None
        
        # Si es un enlace a un mensaje específico
        if len(parts) > 4 and parts[-1].isdigit():
            message_id = int(parts[-1])
            # Manejar enlaces de canales privados (formato c/123456789/123)
            if 'c/' in url:
                chat_id = int(parts[parts.index('c') + 1])
                # Convertir a formato adecuado para canales
                chat_id = int('-100' + str(chat_id))
            else:
                chat_id = parts[-2]
        else:
            chat_id = parts[-1]
            
        logger.info(f"Processing chat_id: {chat_id}, message_id: {message_id}")

        try:
            # Intentar obtener información del chat
            chat = await client.get_chat(chat_id)
            
            # Verificar si el userbot es miembro
            try:
                member = await client.get_chat_member(chat.id, "me")
                is_member = True
            except Exception:
                is_member = False

            if not is_member:
                try:
                    # Intentar unirse si no es miembro
                    await client.join_chat(url)
                    is_member = True
                except Exception as e:
                    if "INVITE_REQUEST_SENT" in str(e):
                        return False, "📩 Se ha enviado una solicitud para unirse al canal.", None, None
                    return False, f"❌ No se pudo unir al canal: {str(e)}", None, None

            # Si es un mensaje específico y somos miembros
            if message_id and is_member:
                success, msg, message = await get_message_info(client, chat.id, message_id)
                if success:
                    return True, msg, message, message_id
                return False, msg, None, None

            return True, "✅ Canal verificado.", chat, message_id

        except Exception as e:
            logger.error(f"Error getting chat info: {e}")
            return False, f"❌ Error al obtener información del chat: {str(e)}", None, None

    except Exception as e:
        logger.error(f"Error in get_chat_info: {e}")
        return False, "❌ Error al procesar el enlace.", None, None

async def process_channel_link(client: Client, message: Message, url: str):
    """Procesa un enlace de canal para añadirlo o unirse"""
    try:
        # Validar el formato del enlace
        if not downloader.validate_url(url):
            return await message.reply(
                "❌ El enlace proporcionado no es válido.\n"
                "Formatos válidos:\n"
                "• Canal privado: https://t.me/+abc123...\n"
                "• Canal público: https://t.me/nombre_canal\n"
                "• Mensaje específico: https://t.me/nombre_canal/123"
            )

        status_msg = await message.reply("🔄 Procesando enlace...")

        try:
            # Intentar unirse al canal
            chat = None
            try:
                chat = await client.join_chat(url)
            except Exception as join_error:
                if "INVITE_REQUEST_SENT" in str(join_error):
                    await status_msg.edit("📩 Se ha enviado una solicitud para unirse al canal. Procesando...")
                elif "INVITE_HASH_EXPIRED" in str(join_error):
                    return await status_msg.edit("❌ El enlace de invitación ha expirado.")
                else:
                    logger.warning(f"Join attempt warning: {str(join_error)}")

            # Si no se pudo unir, intentar obtener info del chat directamente
            if not chat:
                if '+' in url or 'joinchat' in url:
                    return await status_msg.edit("❌ No se pudo unirse al canal. Verifica que el enlace sea válido.")
                
                # Para enlaces públicos, intentar obtener info sin unirse
                chat_id = url.split('/')[-1]
                try:
                    chat = await client.get_chat(chat_id)
                except Exception as e:
                    return await status_msg.edit(f"❌ No se pudo acceder al canal: {str(e)}")

            # Verificar membresía actual
            try:
                member = await client.get_chat_member(chat.id, "me")
                is_member = True
            except Exception:
                is_member = False

            if not is_member:
                return await status_msg.edit(
                    "❌ No se pudo completar la operación.\n"
                    "• Verifica que el enlace sea válido\n"
                    "• Asegúrate de que el userbot tenga permisos para unirse\n"
                    "• Si es un canal privado, espera a que se acepte la solicitud"
                )

            # Registrar el canal en la base de datos
            await db_instance.add_monitored_channel(message.from_user.id, str(chat.id))
            
            await status_msg.edit(
                f"✅ Canal añadido exitosamente:\n"
                f"• Nombre: <b>{escape_html(chat.title)}</b>\n"
                f"• ID: <code>{chat.id}</code>\n\n"
                f"📝 Ahora puedes usar /get_restricted para descargar contenido.",
                parse_mode=ParseMode.HTML
            )

        except Exception as e:
            error_msg = str(e)
            if "INVITE_HASH_EXPIRED" in error_msg:
                await status_msg.edit("❌ El enlace de invitación ha expirado.")
            else:
                await status_msg.edit(f"❌ Error al procesar el canal: {error_msg}")
            logger.error(f"Error processing channel {url}: {error_msg}")

    except Exception as e:
        logger.error(f"Error in process_channel_link: {str(e)}", exc_info=True)
        await message.reply("❌ Ocurrió un error inesperado. Por favor, intenta nuevamente.")

@Client.on_message(filters.text & filters.private)
async def text_gatekeeper(client: Client, message: Message):
    """Manejador principal para todos los mensajes de texto"""
    try:
        user_id = message.from_user.id
        text = message.text.strip()
        
        # No procesar comandos aquí
        if text.startswith('/'):
            return

        # Verificar si es un enlace de Telegram (sea comando o no)
        if "t.me/" in text:
            logger.info(f"Received Telegram link: {text}")
            
            # Mensaje de estado inicial
            status_msg = await message.reply("🔄 Analizando enlace...")
            
            try:
                # Verificar si el enlace tiene formato válido
                if not any(pattern in text for pattern in ['/+', '/joinchat/', 'c/', '/']):
                    return await status_msg.edit(
                        "❌ El enlace proporcionado no es válido.\n\n"
                        "<b>Formatos válidos:</b>\n"
                        "• Canal privado: https://t.me/+abc123...\n"
                        "• Canal público: https://t.me/nombre_canal\n"
                        "• Mensaje específico: https://t.me/nombre_canal/123",
                        parse_mode=ParseMode.HTML
                    )
                    
                # Intentar procesar el enlace
                parts = text.split('/')
                
                # Es un enlace a un mensaje específico
                if len(parts) > 4 and parts[-1].isdigit():
                    message_id = int(parts[-1])
                    chat_id = parts[-2]
                    
                    await status_msg.edit("🔄 Accediendo al contenido...")
                    
                    try:
                        # Usar el userbot para acceder al contenido
                        user_client = client.user_client
                        target_chat = await user_client.get_chat(chat_id)
                        
                        # Verificar membresía
                        try:
                            member = await user_client.get_chat_member(target_chat.id, "me")
                            is_member = True
                        except Exception:
                            is_member = False
                            
                        if not is_member:
                            try:
                                await user_client.join_chat(text)
                                await status_msg.edit("✅ Unido al canal. Accediendo al contenido...")
                            except Exception as e:
                                return await status_msg.edit(f"❌ No se pudo unir al canal: {str(e)}")
                        
                        # Intentar obtener el mensaje
                        target_message = await user_client.get_messages(target_chat.id, message_id)
                        
                        if not target_message:
                            return await status_msg.edit("❌ No se encontró el mensaje específico.")
                        
                        if not target_message.media:
                            return await status_msg.edit("❌ El mensaje no contiene archivos multimedia.")
                        
                        # Procesar el contenido multimedia
                        await process_media_message(client, message, target_message, status_msg)
                        
                    except Exception as e:
                        logger.error(f"Error processing message: {str(e)}")
                        return await status_msg.edit(f"❌ Error al procesar el mensaje: {str(e)}")
                        
                else:
                    # Es un enlace de canal
                    try:
                        success, info_msg, chat, _ = await get_chat_info(client, text)
                        if not success:
                            return await status_msg.edit(info_msg)
                            
                        await status_msg.edit(
                            f"✅ Canal verificado: <b>{chat.title}</b>\n"
                            "📤 Ahora envía el enlace del mensaje específico que quieres descargar.\n"
                            "Ejemplo: https://t.me/nombre_canal/123",
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as e:
                        logger.error(f"Error processing channel: {str(e)}")
                        return await status_msg.edit(f"❌ Error al procesar el canal: {str(e)}")
                        
            except Exception as e:
                logger.error(f"Error in link processing: {str(e)}")
                return await status_msg.edit("❌ Error al procesar el enlace.")
                
        # Manejar otros estados si es necesario
        user_state = await db_instance.get_user_state(user_id)
        if user_state.get("status") != "idle":
            await processing_handler.handle_text_input_for_state(client, message, user_state)
            
    except Exception as e:
        logger.error(f"Error in text_gatekeeper: {str(e)}")
        await message.reply("❌ Ocurrió un error inesperado.")
        
        # Verificar si es un enlace a un mensaje específico
        parts = text.split('/')
        if len(parts) > 4 and parts[-1].isdigit():
            chat_id = parts[-2]
            message_id = int(parts[-1])
            
            try:
                # Usar el userbot para obtener el mensaje
                user_client = client.user_client
                
                # Primero verificar si podemos acceder al chat
                try:
                    chat = await user_client.get_chat(chat_id)
                except Exception as e:
                    logger.error(f"Error getting chat: {e}")
                    return await status_msg.edit("❌ No se puede acceder al chat. Asegúrate de que el userbot sea miembro.")
                
                # Intentar obtener el mensaje
                try:
                    target_message = await user_client.get_messages(chat.id, message_id)
                    if not target_message:
                        return await status_msg.edit("❌ No se encontró el mensaje específico.")
                    
                    if not target_message.media:
                        return await status_msg.edit("❌ El mensaje no contiene archivos multimedia.")
                    
                    # Procesar el mensaje multimedia
                    await process_media_message(client, message, target_message, status_msg)
                    
                except Exception as e:
                    logger.error(f"Error getting message: {e}")
                    return await status_msg.edit(f"❌ Error al obtener el mensaje: {str(e)}")
            
            except Exception as e:
                logger.error(f"Error processing message link: {e}")
                return await status_msg.edit("❌ Error al procesar el enlace del mensaje.")
        else:
            # Si es un enlace de canal, procesarlo normalmente
            await process_channel_link(client, message, text)
    
    # Nueva lógica para diferenciar enlaces
    if downloader.validate_url(text):
        # Si es una URL de Telegram, la manejamos como contenido restringido.
        # Esta es una suposición, podrías querer una lógica más explícita
        # con un comando como /get_restricted
        await message.reply("He detectado un enlace de Telegram. Para descargarlo, por favor usa el comando /get_restricted y sigue las instrucciones.")
        return
    
    if re.search(URL_REGEX, text):
        # Para otras URLs, mantenemos el flujo anterior (que ya no debería usar yt-dlp)
        return await handle_url_input(client, message, text)
    
    # Si no es una URL y no es un estado especial, se asume que es una búsqueda de música
    if user_state.get("status") == "waiting_channel_link":
        await process_channel_link(client, message, text)
        await db_instance.set_user_state(user_id, "idle")
        return
    elif user_state.get("status") != "idle":
        return await processing_handler.handle_text_input_for_state(client, message, user_state)
    else:
        await handle_music_search(client, message, text)

async def handle_url_input(client: Client, message: Message, url: str):
    # Añadimos una guarda para ignorar explícitamente los enlaces de Telegram aquí
    if downloader.validate_url(url):
        await message.reply("He detectado un enlace de Telegram. Para descargarlo, por favor usa el comando /get_restricted y sigue las instrucciones.")
        return

    status_msg = await message.reply("🔎 Analizando enlace...")
    try:
        # Esta sección ahora solo se ejecutará para URLs que NO son de Telegram.
        # Como yt-dlp ya no está, esta llamada debería fallar o estar vacía.
        # La eliminaremos en el futuro, pero por ahora la guarda anterior es suficiente.
        info = await asyncio.to_thread(downloader.get_url_info, url)
        if not info: raise ValueError("No se pudo obtener información del enlace. El bot ya no soporta descargas directas de sitios como YouTube.")
        
        caption = f"<b>📝 Título:</b> {escape_html(info['title'])}\n<b>🕓 Duración:</b> {format_time(info.get('duration'))}"
        temp_info_id = str((await db_instance.search_results.insert_one({'user_id': message.from_user.id, 'data': info, 'created_at': datetime.utcnow()})).inserted_id)
        keyboard = build_detailed_format_menu(temp_info_id, info.get('formats', []))
        await status_msg.delete()
        if thumbnail := info.get('thumbnail'):
            await client.send_photo(message.from_user.id, photo=thumbnail, caption=caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        else:
            await client.send_message(message.from_user.id, caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except AuthenticationError as e:
        await status_msg.edit(f"❌ <b>Error de autenticación:</b>\n<code>{escape_html(str(e))}</code>", parse_mode=ParseMode.HTML)
    except (NetworkError, ValueError) as e:
        await status_msg.edit(f"❌ <b>Error:</b>\n<code>{escape_html(str(e))}</code>", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.critical(f"Error inesperado procesando URL {url}: {e}", exc_info=True)
        await status_msg.edit("❌ <b>Error inesperado del sistema.</b>", parse_mode=ParseMode.HTML)

async def handle_music_search(client: Client, message: Message, query: str):
    status_msg = await message.reply(f"🔎 Buscando música: <code>{escape_html(query)}</code>...", parse_mode=ParseMode.HTML)
    try:
        results = await asyncio.to_thread(downloader.search_music, query, limit=10)
        if not results: return await status_msg.edit("❌ No encontré resultados.")
        search_id = str((await db_instance.search_sessions.insert_one({'user_id': message.from_user.id, 'created_at': datetime.utcnow()})).inserted_id)
        docs = [{'search_id': search_id, 'created_at': datetime.utcnow(), **res} for res in results]
        await db_instance.search_results.insert_many(docs)
        await status_msg.edit("✅ He encontrado esto. Seleccione una opción para descargar:", reply_markup=build_search_results_keyboard(docs, search_id))
    except Exception as e:
        logger.error(f"Error en búsqueda de música para '{query}': {e}", exc_info=True)
        await status_msg.edit(f"❌ Ocurrió un error durante la búsqueda: <code>{escape_html(str(e))}</code>", parse_mode=ParseMode.HTML)

@Client.on_callback_query(filters.regex(r"^set_dlformat_"))
async def set_download_format_callback(client: Client, query: CallbackQuery):
    await query.answer("Preparando tarea...", show_alert=False)
    parts = query.data.split("_")
    temp_info_id, format_id = parts[2], "_".join(parts[3:])
    info_doc = await db_instance.search_results.find_one_and_delete({"_id": ObjectId(temp_info_id)})
    if not info_doc: return await query.message.edit("❌ Esta selección ha expirado.")
    info = info_doc['data']
    file_type = 'audio' if 'audio' in format_id or 'mp3' in format_id else 'video'
    await db_instance.add_task(user_id=query.from_user.id, file_type=file_type, file_name=sanitize_filename(info['title']), url=info.get('webpage_url') or info.get('url'), processing_config={"download_format_id": format_id}, status="queued")
    await query.message.edit(f"✅ <b>¡Enviado a la cola!</b>\n🔗 <code>{escape_html(info['title'])}</code>", parse_mode=ParseMode.HTML)

@Client.on_callback_query(filters.regex(r"^(song_select_|search_page_|cancel_search_)"))
async def search_callbacks_router(client: Client, query: CallbackQuery):
    try:
        await query.answer()
        data = query.data
        if data.startswith("song_select_"): await select_song_from_search(client, query)
        elif data.startswith("search_page_"): await handle_search_pagination(client, query)
        elif data.startswith("cancel_search_"): await cancel_search_session(client, query)
    except MessageNotModified: pass
    except Exception as e: logger.error(f"Error en search_callbacks_router: {e}", exc_info=True)

async def select_song_from_search(client: Client, query: CallbackQuery):
    result_id = query.data.split("_")[2]
    search_result = await db_instance.search_results.find_one({"_id": ObjectId(result_id)})
    if not search_result: return await query.message.edit("❌ Este resultado de búsqueda ha expirado.")
    search_term = search_result.get('search_term')
    display_title = f"{search_result.get('artist', '')} - {search_result.get('title', 'Canción Desconocida')}"
    await query.message.edit(f"🔎 Obteniendo mejor fuente de audio para:\n<code>{escape_html(display_title)}</code>", parse_mode=ParseMode.HTML)
    try:
        url_info = await asyncio.to_thread(downloader.get_url_info, f"ytsearch1:{search_term}")
        if not url_info or not (url_info.get('webpage_url') or url_info.get('url')): return await query.message.edit("❌ No pude encontrar una fuente de audio descargable.")
        final_filename = sanitize_filename(f"{search_result['artist']} - {search_result['title']}")
        await db_instance.add_task(user_id=query.from_user.id, file_type='audio', file_name=f"{final_filename}.mp3", url=url_info.get('webpage_url'), status="queued", processing_config={"download_format_id": downloader.get_best_audio_format_id(url_info.get('formats', [])), "audio_tags": {'title': search_result['title'], 'artist': search_result['artist'], 'album': search_result.get('album')}})
        await query.message.edit(f"✅ <b>¡Enviado a la cola!</b>\n🎧 <code>{escape_html(display_title)}</code>", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error al procesar selección de canción: {e}", exc_info=True)
        await query.message.edit(f"❌ Error al obtener la fuente: <code>{escape_html(str(e))}</code>", parse_mode=ParseMode.HTML)

async def handle_search_pagination(client: Client, query: CallbackQuery):
    _, search_id, page_str = query.data.split("_")
    page = int(page_str)
    results = await db_instance.search_results.find({"search_id": search_id}).sort('created_at', 1).to_list(length=100)
    if not results: return await query.message.edit("❌ La sesión de búsqueda ha expirado.")
    await query.message.edit_reply_markup(reply_markup=build_search_results_keyboard(results, search_id, page))

async def cancel_search_session(client: Client, query: CallbackQuery):
    await query.message.delete()

# --- Manejadores para canales restringidos ---

@Client.on_message(filters.command("add_channel") & filters.private)
async def add_channel_command(client: Client, message: Message):
    """Inicia el proceso de añadir un canal restringido"""
    user_id = message.from_user.id
    text = message.text.split(maxsplit=1)

    # Si se proporciona el enlace directamente con el comando
    if len(text) > 1:
        url = text[1].strip()
        return await process_channel_link(client, message, url)

    # Si no hay enlace, establecer estado de espera
    await db_instance.set_user_state(user_id, "waiting_channel_link")
    
    await message.reply(
        "🔒 <b>Añadir Canal Restringido</b>\n\n"
        "Por favor, envíe el enlace del canal.\n"
        "Formatos válidos:\n"
        "• Canal privado: https://t.me/+abc123...\n"
        "• Canal público: https://t.me/nombre_canal",
        parse_mode=ParseMode.HTML
    )

@Client.on_message(filters.command("list_channels") & filters.private)
async def list_channels_command(client: Client, message: Message):
    """Lista los canales monitoreados del usuario"""
    user_id = message.from_user.id
    
    channels = await db_instance.get_monitored_channels(user_id)
    
    if not channels:
        return await message.reply(
            "📝 <b>Canales Monitoreados</b>\n\n"
            "No tienes canales configurados para monitoreo.\n"
            "Usa /add_channel para añadir uno.",
            parse_mode=ParseMode.HTML
        )
    
    response = ["📝 <b>Canales Monitoreados:</b>\n"]
    
    for i, channel in enumerate(channels, 1):
        try:
            chat = await client.get_chat(channel["channel_id"])
            channel_info = (
                f"{i}. <b>{escape_html(chat.title)}</b>\n"
                f"   • ID: <code>{channel['channel_id']}</code>\n"
                f"   • Añadido: {channel['added_on'].strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
        except Exception:
            channel_info = (
                f"{i}. <b>Canal no disponible</b>\n"
                f"   • ID: <code>{channel['channel_id']}</code>\n"
                f"   • Añadido: {channel['added_on'].strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
        response.append(channel_info)
    
    response.append("\nUsa /add_channel para añadir más canales.")
    
    await message.reply("\n".join(response), parse_mode=ParseMode.HTML)

def format_size(size: int) -> str:
    """Formatea el tamaño en bytes a una forma legible"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"

def format_time(seconds: float) -> str:
    """Formatea el tiempo en segundos a una forma legible"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.1f}m"
    hours = minutes / 60
    minutes = minutes % 60
    return f"{hours:.0f}h {minutes:.0f}m"

async def show_progress(current: int, total: int, status_msg: Message, action: str, start_time: float, user_client=None):
    """Muestra una barra de progreso detallada con estadísticas"""
    if total == 0:
        return
    
    try:
        # Evitar actualizaciones demasiado frecuentes (cada 0.5 segundos)
        now = asyncio.get_event_loop().time()
        if hasattr(show_progress, 'last_update'):
            if now - show_progress.last_update < 0.5:
                return
        show_progress.last_update = now
        
        elapsed_time = now - start_time
        if elapsed_time == 0:
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
                if hasattr(show_progress, 'user_info'):
                    me = show_progress.user_info
                else:
                    me = await user_client.get_me()
                    show_progress.user_info = me
            except:
                pass
        
        progress_bar = (
            f"Task is being Processed!\n"
            f"[{'▤' * done}{'□' * pending}] {percent:.2f}%\n"
            f"┠ Processed: {format_size(current)} of {format_size(total)}\n"
            f"┠ File: 1/1\n"
            f"┠ Status: #TelegramDownload\n"
            f"┠ ETA: {format_time(eta)}\n"
            f"┠ Speed: {format_size(speed)}/s\n"
            f"┠ Elapsed: {format_time(elapsed_time)}\n"
            f"┠ Engine: {me.first_name}\n"
            f"┖ ID: {me.id}"
        )
        
        await status_msg.edit(progress_bar, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.debug(f"Error showing progress: {e}")

async def get_media_info(message: Message) -> dict:
    """Obtiene información detallada del archivo multimedia"""
    info = {
        "file_name": "",
        "mime_type": "",
        "file_size": 0,
        "duration": 0,
        "width": 0,
        "height": 0,
        "type": ""
    }
    
    if message.video:
        media = message.video
        info["type"] = "video"
        info["duration"] = media.duration
        info["width"] = media.width
        info["height"] = media.height
    elif message.document:
        media = message.document
        info["type"] = "document"
    elif message.audio:
        media = message.audio
        info["type"] = "audio"
        info["duration"] = media.duration
    elif message.photo:
        media = message.photo
        info["type"] = "photo"
        info["width"] = media.width
        info["height"] = media.height
    else:
        return info

    info["file_name"] = getattr(media, "file_name", f"{info['type']}_{int(time.time())}")
    info["mime_type"] = getattr(media, "mime_type", "")
    info["file_size"] = getattr(media, "file_size", 0)
    
    return info

async def process_media_message(client: Client, original_message: Message, target_message: Message, status_msg: Message):
    """Procesa un mensaje que contiene media para descargarlo y reenviarlo"""
    try:
        # Usar el userbot para las operaciones
        user_client = client.user_client
        start_time = asyncio.get_event_loop().time()
        
        # Obtener información del archivo
        media_info = await get_media_info(target_message)
        
        # Asegurar que tenemos un nombre de archivo válido
        if not media_info['file_name']:
            media_info['file_name'] = f"{media_info['type']}_{int(time.time())}"
        
        # Mostrar mensaje inicial con información detallada
        initial_message = (
            f"📥 <b>Preparando Descarga</b>\n\n"
            f"📁 <b>Archivo:</b> {media_info['file_name']}\n"
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
        
        # Preparar carpeta temporal si es necesario
        temp_path = os.path.join(os.getcwd(), "downloads")
        os.makedirs(temp_path, exist_ok=True)
        
        # Descargar el archivo usando el userbot
        start_dl_time = asyncio.get_event_loop().time()
        file_path = await user_client.download_media(
            target_message,
            file_name=os.path.join(temp_path, media_info['file_name']),
            progress=show_progress,
            progress_args=(status_msg, "Descargando", start_dl_time)
        )
        
        if not file_path:
            return await status_msg.edit("❌ No se pudo descargar el archivo.")
        
        # Mostrar resumen de la descarga
        dl_time = asyncio.get_event_loop().time() - start_dl_time
        dl_speed = media_info['file_size'] / dl_time if dl_time > 0 else 0
        
        await status_msg.edit(
            f"✅ <b>Descarga completada</b>\n\n"
            f"⚡️ <b>Velocidad promedio:</b> {format_size(dl_speed)}/s\n"
            f"⏱ <b>Tiempo total:</b> {format_time(dl_time)}\n\n"
            f"🔄 Preparando para subir...",
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(1)
        
        # Preparar metadatos para la subida
        caption = target_message.caption or f"@{original_message.from_user.username}"
        thumb = None
        
        if target_message.video and hasattr(target_message.video, 'thumbs'):
            thumb = await user_client.download_media(target_message.video.thumbs[0])
        
        # Iniciar subida
        start_up_time = asyncio.get_event_loop().time()
        # Enviar el archivo según su tipo
        start_up_time = asyncio.get_event_loop().time()
        try:
            if target_message.video:
                # Preparar el thumbnail para videos
                if target_message.video and target_message.video.thumbs:
                    try:
                        thumb = await user_client.download_media(
                            target_message.video.thumbs[0],
                            file_name=f"thumb_{int(time.time())}.jpg"
                        )
                    except Exception as e:
                        logger.warning(f"Error descargando thumbnail: {e}")
                        thumb = None

                await user_client.send_video(
                    original_message.chat.id,
                    file_path,
                    thumb=thumb,
                    duration=media_info['duration'],
                    width=media_info['width'],
                    height=media_info['height'],
                    caption=caption,
                    progress=show_progress,
                    progress_args=(status_msg, "Subiendo video", start_up_time, user_client),
                    supports_streaming=True
                )
            elif target_message.document:
                await user_client.send_document(
                    original_message.chat.id,
                    file_path,
                    thumb=thumb,
                    caption=caption,
                    progress=show_progress,
                    progress_args=(status_msg, "Subiendo documento", start_up_time)
                )
            elif target_message.audio:
                await user_client.send_audio(
                    original_message.chat.id,
                    file_path,
                    duration=media_info['duration'],
                    caption=caption,
                    progress=show_progress,
                    progress_args=(status_msg, "Subiendo audio", start_up_time)
                )
            elif target_message.photo:
                await user_client.send_photo(
                    original_message.chat.id,
                    file_path,
                    caption=caption,
                    progress=show_progress,
                    progress_args=(status_msg, "Subiendo foto", start_up_time)
                )
                
            # Mostrar resumen final
            total_time = asyncio.get_event_loop().time() - start_time
            up_time = asyncio.get_event_loop().time() - start_up_time
            up_speed = media_info['file_size'] / up_time if up_time > 0 else 0
            
            me = await user_client.get_me()
            
            await status_msg.edit(
                f"Task has been Completed!\n\n"
                f"┠ File: {media_info['file_name']}\n"
                f"┠ Total Files: 1\n"
                f"┠ Size: {format_size(media_info['file_size'])}\n"
                f"┠ Elapsed: {format_time(total_time)}\n"
                f"┠ Mode: Telegram\n"
                f"┠ Engine: {me.first_name}\n"
                f"┖ ID: {me.id}"
            )
            
        except Exception as e:
            logger.error(f"Error sending file: {e}")
            await status_msg.edit(
                f"❌ <b>Error al enviar el archivo</b>\n\n"
                f"<code>{str(e)}</code>",
                parse_mode=ParseMode.HTML
            )
            
        finally:
            # Limpiar archivos temporales
            try:
                os.remove(file_path)
                if thumb:
                    os.remove(thumb)
            except Exception as e:
                logger.error(f"Error cleaning temporary files: {e}")
        
        # Limpiar
        try:
            os.remove(file_path)
        except:
            pass
        
        await status_msg.edit("✅ Contenido procesado exitosamente")
        
    except Exception as e:
        logger.error(f"Error processing media message: {e}")
        await status_msg.edit(f"❌ Error al procesar el contenido: {str(e)}")

async def progress_callback(current: int, total: int, status_msg: Message, action: str):
    """Callback para mostrar el progreso de descarga/subida"""
    try:
        percent = current * 100 / total
        status_text = f"{action}: {percent:.1f}%\n"
        status_text += f"[{'=' * int(percent // 5)}{'.' * (20 - int(percent // 5))}]"
        await status_msg.edit(status_text)
    except:
        pass

@Client.on_message(filters.command("get_restricted") & filters.private)
async def get_restricted_command(client: Client, message: Message):
    """Inicia el proceso de obtener contenido de un canal restringido"""
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
                "• Mensaje específico: https://t.me/nombre_canal/123",
                parse_mode=ParseMode.HTML
            )
            
        url = text[1].strip()
        
        # Validar formato del enlace
        if not downloader.validate_url(url):
            return await message.reply(
                "❌ El enlace proporcionado no es válido.\n"
                "<b>Formatos válidos:</b>\n"
                "• Canal privado: https://t.me/+abc123...\n"
                "• Canal público: https://t.me/nombre_canal\n"
                "• Mensaje específico: https://t.me/nombre_canal/123",
                parse_mode=ParseMode.HTML
            )
            
        # Mostrar mensaje de estado inicial
        status_msg = await message.reply("🔄 Procesando enlace...")
        
        # Obtener información del chat/mensaje
        success, info_msg, data, message_id = await get_chat_info(client, url)
        
        if not success:
            return await status_msg.edit(info_msg)
            
        # Si es un mensaje específico
        if isinstance(data, Message):
            if not data.media:
                return await status_msg.edit("❌ El mensaje no contiene archivos multimedia.")
            await process_media_message(client, message, data, status_msg)
            
        # Si es un chat
        elif isinstance(data, Chat):
            if message_id:  # Si teníamos un message_id en el enlace
                success, msg, target_message = await get_message_info(client, data.id, message_id)
                if success and target_message:
                    await process_media_message(client, message, target_message, status_msg)
                else:
                    await status_msg.edit(msg)
            else:
                await status_msg.edit(
                    f"✅ Conectado al canal: <b>{escape_html(data.title)}</b>\n"
                    "📤 Ahora envía el enlace del mensaje específico que quieres descargar.\n"
                    "Ejemplo: https://t.me/nombre_canal/123",
                    parse_mode=ParseMode.HTML
                )

        url = text[1].strip()
        
        # Validar el formato del enlace
        if not downloader.validate_url(url):
            return await message.reply(
                "❌ El enlace proporcionado no es válido.\n"
                "Formatos válidos:\n"
                "• Canal privado: https://t.me/+abc123...\n"
                "• Canal público: https://t.me/nombre_canal\n"
                "• Mensaje: https://t.me/nombre_canal/123"
            )

        status_msg = await message.reply("🔄 Procesando enlace...")
        
        try:
            # Primero intentar unirse si es necesario
            try:
                await client.join_chat(url)
                logger.info(f"Joined successfully: {url}")
            except Exception as join_error:
                if "INVITE_REQUEST_SENT" in str(join_error):
                    return await status_msg.edit("❌ Se ha enviado una solicitud para unirse al canal. Por favor, espere a ser aceptado.")
                logger.warning(f"Join attempt failed: {str(join_error)}")
                # Continuamos aunque falle el join, podría estar ya unido

            # Intentar obtener info del chat
            if '/+' in url or '/joinchat/' in url:  # Enlaces de invitación
                chat = await client.get_chat(url)
            else:  # Enlaces públicos o mensajes específicos
                parts = url.split('/')
                chat_id = parts[-2] if len(parts) > 4 else parts[-1]
                chat = await client.get_chat(chat_id)

            # Verificar membresía
            try:
                member = await client.get_chat_member(chat.id, "me")
                is_member = True
            except Exception:
                is_member = False

            if not is_member:
                return await status_msg.edit("❌ No se pudo unir al canal o no tienes acceso. Verifica que el enlace sea válido y que el userbot tenga los permisos necesarios.")

            # Procesar según el tipo de enlace
            if len(url.split('/')) > 4:  # Es un mensaje específico
                msg_id = int(url.split('/')[-1])
                try:
                    msg = await client.get_messages(chat.id, msg_id)
                    if msg and msg.media:
                        await status_msg.edit("✅ Mensaje encontrado. Iniciando descarga...")
                        # Aquí iría la lógica de descarga
                        await client.copy_message(
                            chat_id=message.chat.id,
                            from_chat_id=chat.id,
                            message_id=msg_id
                        )
                        await status_msg.delete()
                    else:
                        await status_msg.edit("❌ El mensaje no existe o no contiene archivos multimedia.")
                except Exception as e:
                    await status_msg.edit(f"❌ Error al obtener el mensaje: {str(e)}")
            else:  # Es un enlace de canal
                await status_msg.edit(
                    f"✅ Conectado al canal: <b>{escape_html(chat.title)}</b>\n"
                    "📤 Por favor, ahora envía el enlace del mensaje específico que quieres descargar.\n"
                    "Ejemplo: https://t.me/nombre_canal/123",
                    parse_mode=ParseMode.HTML
                )

        except Exception as e:
            error_msg = str(e)
            if "INVITE_HASH_EXPIRED" in error_msg:
                await status_msg.edit("❌ El enlace de invitación ha expirado.")
            elif "INVITE_REQUEST_SENT" in error_msg:
                await status_msg.edit("📩 Se ha enviado una solicitud para unirse al canal. Por favor, espere a ser aceptado.")
            else:
                await status_msg.edit(f"❌ Error al procesar el enlace: {error_msg}")
            logger.error(f"Error processing link {url}: {error_msg}")

    except Exception as e:
        logger.error(f"Error in get_restricted_command: {str(e)}", exc_info=True)
        await message.reply("❌ Ocurrió un error inesperado. Por favor, intenta nuevamente.")