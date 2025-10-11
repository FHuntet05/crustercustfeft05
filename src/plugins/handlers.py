# --- START OF FILE src/plugins/handlers.py ---

import logging
import re
from datetime import datetime
import asyncio
import os

from pyrogram import Client, filters, StopPropagation
from pyrogram.types import Message, CallbackQuery
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
async def process_channel_link(client: Client, message: Message, url: str):
    """Procesa un enlace de canal para añadirlo o unirse"""
    try:
        # Validar el formato del enlace
        if not downloader.validate_url(url):
            return await message.reply(
                "❌ El enlace proporcionado no es válido.\n"
                "Formatos válidos:\n"
                "• Canal privado: https://t.me/+abc123...\n"
                "• Canal público: https://t.me/nombre_canal"
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

@Client.on_message(filters.text & filters.private, group=2)
async def text_gatekeeper(client: Client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text.startswith('/'):
        return

    user_state = await db_instance.get_user_state(user_id)
    if user_state.get("status") != "idle":
        if user_state.get("status") == "waiting_channel_link":
            await process_channel_link(client, message, text)
            await db_instance.set_user_state(user_id, "idle")
            return
        return await processing_handler.handle_text_input_for_state(client, message, user_state)
    
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
    
    # Si no es una URL, se asume que es una búsqueda de música
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