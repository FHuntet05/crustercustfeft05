import logging
import re
from datetime import datetime
import asyncio
import os

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from pyrogram.errors import MessageNotModified
from bson.objectid import ObjectId

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import (build_processing_menu, build_search_results_keyboard,
                                   build_detailed_format_menu, build_profiles_keyboard,
                                   build_confirmation_keyboard, build_batch_profiles_keyboard,
                                   build_join_selection_keyboard, build_zip_selection_keyboard)
from src.helpers.utils import (get_greeting, escape_html, sanitize_filename,
                               format_time, format_task_details_rich)
from src.core import downloader
from src.core.exceptions import AuthenticationError
from . import processing_handler

logger = logging.getLogger(__name__)

URL_REGEX = r'(https?://[^\s]+)'
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")
if ADMIN_USER_ID and ADMIN_USER_ID.isdigit():
    ADMIN_USER_ID = int(ADMIN_USER_ID)

@Client.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    user = message.from_user
    greeting_prefix = get_greeting(user.id)
    
    # Limpiar estado obsoleto al iniciar
    await reset_user_state_if_needed(client, user.id)

    start_message = (
        f"A sus órdenes, {greeting_prefix}, bienvenido a la <b>Suite de Medios v15.0 (Estable)</b>.\n\n"
        "Sistema de estado reiniciado. Estoy listo para nuevas tareas.\n\n"
        "<b>Comandos Principales:</b>\n"
        "• /panel - Muestra su mesa de trabajo con detalles.\n"
        "• /p <code>[ID]</code> - Abre el menú de una tarea.\n"
        "• /p clean <code>[ID]</code> - Elimina una tarea específica.\n"
        "• /join, /zip, /p_all - Acciones en lote.\n"
        "• /profiles - Gestiona sus perfiles.\n\n"
        "Envíe un archivo, enlace de YouTube o un texto para buscar música."
    )
    await message.reply(start_message, parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("panel") & filters.private)
async def panel_command(client: Client, message: Message):
    user = message.from_user
    greeting_prefix = get_greeting(user.id).replace(',', '')
    pending_tasks = await db_instance.get_pending_tasks(user.id)
    
    if not pending_tasks:
        text = f"✅ ¡{greeting_prefix}, su mesa de trabajo está vacía!"
        return await message.reply(text, parse_mode=ParseMode.HTML)
    
    response_lines = [f"📋 <b>{greeting_prefix}, su mesa de trabajo actual:</b>"]
    for i, task in enumerate(pending_tasks):
        response_lines.append(format_task_details_rich(task, i + 1))

    response_lines.append(f"\nUse /p <code>[ID]</code> para configurar una tarea (ej: <code>/p 1</code>).")
    response_lines.append(f"Use /p clean para limpiar todas las tareas.")
    await message.reply("\n".join(response_lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@Client.on_message(filters.command("p") & filters.private)
async def process_command(client: Client, message: Message):
    user = message.from_user
    parts = message.text.split()
    if len(parts) < 2:
        return await message.reply("Uso: `/p [ID]` o `/p clean` o `/p clean [ID]`.", parse_mode=ParseMode.MARKDOWN)
    
    action = parts[1]
    if action.lower() == "clean":
        if len(parts) > 2 and parts[2].isdigit():
            task_idx_to_delete = int(parts[2]) - 1
            pending_tasks = await db_instance.get_pending_tasks(user.id)
            if 0 <= task_idx_to_delete < len(pending_tasks):
                task_to_delete = pending_tasks[task_idx_to_delete]
                await db_instance.delete_task_by_id(str(task_to_delete['_id']))
                await message.reply(f"🗑️ Tarea #{task_idx_to_delete + 1} eliminada del panel.")
            else:
                await message.reply("❌ ID de tarea inválido.")
        else:
            deleted_count = await db_instance.delete_all_pending_tasks(user.id)
            await message.reply(f"🗑️ Panel limpiado. Se eliminaron {deleted_count.deleted_count} tareas.")
        return

    if not action.isdigit():
        return await message.reply("El ID debe ser un número. Use `/panel` para ver los IDs de sus tareas.")
        
    task_index = int(action) - 1
    pending_tasks = await db_instance.get_pending_tasks(user.id)

    if 0 <= task_index < len(pending_tasks):
        task = pending_tasks[task_index]
        await processing_handler.open_task_menu_from_p(client, message, str(task['_id']))
    else:
        await message.reply(f"❌ ID inválido. Tiene {len(pending_tasks)} tareas en su panel.")

async def reset_user_state_if_needed(client: Client, user_id: int):
    """Limpia un estado de usuario si no es 'idle', borrando el mensaje asociado."""
    user_state = await db_instance.get_user_state(user_id)
    if user_state.get("status") != "idle":
        logger.warning(f"Reseteando estado obsoleto '{user_state.get('status')}' para el usuario {user_id}.")
        if source_message_id := user_state.get("data", {}).get("source_message_id"):
            try:
                await client.delete_messages(user_id, source_message_id)
            except Exception: pass
        await db_instance.set_user_state(user_id, "idle")

@Client.on_message(filters.media & filters.private, group=1)
async def media_gatekeeper(client: Client, message: Message):
    """Gatekeeper para todos los archivos multimedia."""
    user_id = message.from_user.id
    user_state = await db_instance.get_user_state(user_id)
    
    # Si el usuario está en medio de una configuración, delegar al manejador de estado
    if user_state.get("status") != "idle":
        await processing_handler.handle_media_input_for_state(client, message, user_state)
        return

    # Si no, es una nueva tarea
    metadata, status = {}, "pending_processing"
    reply_message_text = "✅ Archivo recibido y añadido al panel."
    
    media = message.video or message.audio or message.document
    file_type = 'video' if message.video else 'audio' if message.audio else 'document'

    if file_type == 'document':
        status = "pending_metadata"
        reply_message_text = "✅ Documento recibido. En cola para análisis de metadatos."
    
    file_size = media.file_size
    final_file_name = sanitize_filename(getattr(media, 'file_name', f"{file_type}_{int(datetime.utcnow().timestamp())}"))
    
    if file_type == 'video': metadata = {"resolution": f"{media.width}x{media.height}" if media.width else None, "duration": media.duration}
    elif file_type == 'audio': metadata = {"duration": media.duration}

    metadata['size'] = file_size
    
    task_id = await db_instance.add_task(user_id=user_id, file_type=file_type, file_name=final_file_name, file_id=media.file_id,
                                       file_size=file_size, status=status, metadata=metadata)
    if not task_id: return await message.reply("❌ Error al registrar la tarea en la DB.")

    status_msg = await message.reply(reply_message_text, parse_mode=ParseMode.HTML)
    
    if status == "pending_processing":
        count = await db_instance.tasks.count_documents({'user_id': user_id, 'status': 'pending_processing'})
        await status_msg.edit(f"Añadido al panel como tarea <b>#{count}</b>.\nUse `/p {count}` para configurar.", parse_mode=ParseMode.HTML)


async def handle_url_input(client: Client, message: Message, url: str):
    user = message.from_user
    status_message = await message.reply("🔎 Analizando enlace...", parse_mode=ParseMode.HTML)
    
    try:
        info = await asyncio.to_thread(downloader.get_url_info, url)
        if not info:
            raise ValueError("No pude obtener información de ese enlace.")
        
        caption = (f"<b>📝 Nombre:</b> {escape_html(info['title'])}\n"
                   f"<b>🕓 Duración:</b> {format_time(info.get('duration'))}\n"
                   f"<b>📢 Canal:</b> {escape_html(info.get('uploader'))}\n\n"
                   "Elija la calidad para la descarga:")
        
        # Guardar la info de URL para después de la selección
        url_info_id = str((await db_instance.search_results.insert_one({'user_id': user.id, 'data': info, 'created_at': datetime.utcnow()})).inserted_id)
        
        keyboard = build_detailed_format_menu(url_info_id, info['formats'])
        
        if info.get('thumbnail'):
            await status_message.delete()
            await client.send_photo(user.id, photo=info['thumbnail'], caption=caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        else:
            await status_message.edit_text(caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    except AuthenticationError as e:
        await status_message.edit_text(f"❌ <b>Error de Autenticación</b>: {e}", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error procesando URL {url}: {e}")
        await status_message.edit_text(f"❌ Ocurrió un error: <code>{escape_html(str(e))}</code>")


async def handle_music_search(client: Client, message: Message, query: str):
    user = message.from_user
    status_message = await message.reply(f"🔎 Buscando música: <code>{escape_html(query)}</code>...", parse_mode=ParseMode.HTML)
    
    search_results = await asyncio.to_thread(downloader.search_music, query, limit=10)
    if not search_results:
        return await status_message.edit_text("❌ No encontré resultados para su búsqueda.")

    search_id = str((await db_instance.search_sessions.insert_one({'user_id': user.id, 'created_at': datetime.utcnow()})).inserted_id)
    docs_to_insert = [{'search_id': search_id, 'created_at': datetime.utcnow(), **res} for res in search_results]
    await db_instance.search_results.insert_many(docs_to_insert)

    keyboard = build_search_results_keyboard(docs_to_insert, search_id, page=1)
    await status_message.edit_text("✅ He encontrado esto. Seleccione una para descargar:", reply_markup=keyboard)


@Client.on_message(filters.text & filters.private, group=2)
async def text_gatekeeper_handler(client: Client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text.startswith('/'): return # Los comandos se manejan por separado

    user_state = await db_instance.get_user_state(user_id)
    if user_state.get("status") != "idle":
        await processing_handler.handle_text_input_for_state(client, message, user_state)
        return

    url_match = re.search(URL_REGEX, text)
    if url_match:
        return await handle_url_input(client, message, url_match.group(0))

    await handle_music_search(client, message, text)

@Client.on_callback_query(filters.regex(r"^set_dlformat_"))
async def set_download_format_callback(client: Client, query: CallbackQuery):
    """Callback para cuando el usuario selecciona un formato de descarga de una URL."""
    await query.answer("Preparando tarea...")
    parts = query.data.split("_")
    url_info_id, format_id = parts[2], "_".join(parts[3:])

    url_info_doc = await db_instance.search_results.find_one_and_delete({"_id": ObjectId(url_info_id)})
    if not url_info_doc:
        return await query.message.edit_text("❌ Esta selección ha expirado.")
    
    info = url_info_doc['data']
    task_filename = sanitize_filename(info.get('title', 'descarga_url'))
    
    task_id = await db_instance.add_task(
        user_id=query.from_user.id,
        file_type='video' if 'bestvideo' in format_id else 'audio',
        file_name=task_filename,
        url=info['url'],
        processing_config={"download_format_id": format_id},
        url_info=info,
        status="queued" # Las descargas de URL se ponen en cola directamente
    )

    await query.message.edit_text(f"✅ <b>¡Enviado a la cola!</b>\n\n🔗 <code>{escape_html(task_filename)}</code>\n\nSe procesará en breve.", parse_mode=ParseMode.HTML)


# Agrupar los callbacks para que Pyrogram los maneje con sus respectivos manejadores
# Esto asegura que el filtro regex no entre en conflicto con los decoradores de funciones
@Client.on_callback_query(filters.regex(r"^(p_open_|task_|config_|set_)"))
async def main_config_callbacks(client: Client, query: CallbackQuery):
    data = query.data
    if data.startswith("p_open_"): await processing_handler.open_task_menu_callback(client, query)
    elif data.startswith("task_"): await processing_handler.handle_task_actions(client, query)
    elif data.startswith("config_"): await processing_handler.show_config_menu_and_set_state(client, query)
    elif data.startswith("set_"): await processing_handler.set_value_callback(client, query)

@Client.on_callback_query(filters.regex(r"^(song_select_|search_page_|cancel_search_)"))
async def search_callbacks(client: Client, query: CallbackQuery):
    data = query.data
    if data.startswith("song_select_"): await processing_handler.select_song_from_search(client, query)
    elif data.startswith("search_page_"): await processing_handler.handle_search_pagination(client, query)
    elif data.startswith("cancel_search_"): await processing_handler.cancel_search_session(client, query)