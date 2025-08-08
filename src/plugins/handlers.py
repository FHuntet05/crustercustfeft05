import logging
import re
from datetime import datetime
import asyncio
import os

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InputMediaPhoto
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
from src.core.ffmpeg import get_media_info
from . import processing_handler

logger = logging.getLogger(__name__)

URL_REGEX = r'(https?://[^\s]+)'

@Client.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    user = message.from_user
    greeting_prefix = get_greeting(user.id)
    await db_instance.get_user_settings(user.id)
    start_message = (
        f"A sus órdenes, {greeting_prefix}, bienvenido a la <b>Suite de Medios v12.1</b>.\n\n"
        "Esta versión se enfoca en la <b>estabilidad y corrección de errores</b>.\n\n"
        "<b>Comandos Principales:</b>\n"
        "• /panel - Muestra su mesa de trabajo con detalles.\n"
        "• /p <code>[ID]</code> - Abre el menú de una tarea.\n"
        "• /p clean <code>[ID]</code> - Elimina una tarea específica.\n"
        "• /join - Une videos del panel.\n"
        "• /zip - Comprime tareas del panel en un ZIP.\n"
        "• /p_all - Procesa todas las tareas del panel a la vez.\n"
        "• /profiles - Gestiona sus perfiles.\n\n"
        "Envíe un archivo, un enlace de YouTube, o un texto para buscar música."
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
    response_lines.append(f"Use /join para unir videos o /zip para comprimir.")
    response_lines.append(f"Use /p_all para procesar todo el panel.")
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
            return await message.reply(
                "¿Seguro que desea eliminar TODAS las tareas de su panel?",
                reply_markup=build_confirmation_keyboard("panel_delete_all_confirm", "panel_delete_all_cancel")
            )
        return

    if not action.isdigit():
        return await message.reply("El ID debe ser un número. Use `/panel` para ver los IDs de sus tareas.")
        
    task_index = int(action) - 1
    pending_tasks = await db_instance.get_pending_tasks(user.id)

    if 0 <= task_index < len(pending_tasks):
        task = pending_tasks[task_index]
        await processing_handler.open_task_menu_from_p(client, message, str(task['_id']))
    else:
        await message.reply(f"❌ ID inválido. Tiene {len(pending_tasks)} tareas en su panel. Use un número entre 1 y {len(pending_tasks)}.")

@Client.on_message(filters.media & filters.private, group=1)
async def any_file_handler(client: Client, message: Message):
    user = message.from_user
    
    if hasattr(client, 'user_data') and client.user_data.get(user.id, {}).get("active_config"):
        logger.info(f"Media Gatekeeper: Petición de media detectada para el usuario {user.id} en modo config. Cediendo el control.")
        message.stop_propagation()
        return

    original_media_object, file_type = None, None
    if message.video: original_media_object, file_type = message.video, 'video'
    elif message.audio: original_media_object, file_type = message.audio, 'audio'
    elif message.document: original_media_object, file_type = message.document, 'document'
    
    if not original_media_object: return

    final_file_name = sanitize_filename(getattr(original_media_object, 'file_name', f"Archivo_{file_type}_{int(datetime.utcnow().timestamp())}"))
    
    task_id = await db_instance.add_task(
        user_id=user.id, file_type=file_type, file_name=final_file_name,
        file_id=original_media_object.file_id, file_size=original_media_object.file_size
    )

    if task_id:
        status_msg = await message.reply("🔧 Analizando metadatos del archivo...")
        temp_path = None
        try:
            temp_path = await message.download(in_memory=True)
            media_info = get_media_info(temp_path.name)
            if media_info:
                stream = next((s for s in media_info.get('streams', []) if s.get('codec_type') == 'video'),
                              next((s for s in media_info.get('streams', []) if s.get('codec_type') == 'audio'), {}))
                duration = float(stream.get('duration', 0)) or float(media_info.get('format', {}).get('duration', 0))
                resolution = f"{stream.get('width')}x{stream.get('height')}" if stream.get('width') else None
                await db_instance.tasks.update_one(
                    {"_id": task_id},
                    {"$set": {
                        "file_metadata.duration": duration,
                        "file_metadata.resolution": resolution
                    }}
                )
        except Exception as e:
            logger.warning(f"No se pudieron extraer metadatos para la nueva tarea {task_id}: {e}")
        finally:
            if temp_path and os.path.exists(temp_path.name):
                os.remove(temp_path.name)
        
        user_presets = await db_instance.get_user_presets(user.id)
        if user_presets:
            keyboard = build_profiles_keyboard(str(task_id), user_presets)
            await status_msg.edit("✅ Archivo recibido y añadido al panel. ¿Desea aplicar un perfil?", reply_markup=keyboard)
        else:
            count = await db_instance.tasks.count_documents({'user_id': user.id, 'status': 'pending_processing'})
            await status_msg.edit(f"✅ Archivo recibido y añadido al panel como tarea <b>#{count}</b>.\nUse `/p {count}` para configurarlo.", parse_mode=ParseMode.HTML)
    else:
        await message.reply("❌ Hubo un error al registrar la tarea en la base de datos.")

async def handle_url_input(client: Client, message: Message, url: str):
    user = message.from_user
    await db_instance.set_user_state(user.id, "busy")
    status_message = await message.reply("🔎 Analizando enlace de YouTube...", parse_mode=ParseMode.HTML)
    
    try:
        info = await asyncio.to_thread(downloader.get_url_info, url)
        if not info or not info.get('formats'):
            await db_instance.set_user_state(user.id, "idle")
            return await status_message.edit_text("❌ No pude obtener información de ese enlace. Puede que sea privado o esté restringido.")

        await db_instance.set_user_state(user.id, "awaiting_quality_selection", {"url_info": info})
        
        caption_parts = [
            f"<b>📝 Nombre:</b> {escape_html(info['title'])}",
            f"<b>🕓 Duración:</b> {format_time(info.get('duration'))}",
            f"<b>📢 Canal:</b> {escape_html(info.get('uploader'))}",
            "\nElija la calidad para la descarga:"
        ]
        caption = "\n".join(caption_parts)
        
        temp_task_id_for_menu = "url_selection"
        keyboard = build_detailed_format_menu(temp_task_id_for_menu, info['formats'])
        
        try:
            if info.get('thumbnail'):
                await status_message.delete()
                await client.send_photo(chat_id=user.id, photo=info['thumbnail'], caption=caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            else:
                await status_message.edit_text(caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Error enviando menú de calidad: {e}. Enviando como texto.")
            await status_message.edit_text(caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    except Exception as e:
        await db_instance.set_user_state(user.id, "idle")
        logger.error(f"Error procesando URL {url}: {e}")
        await status_message.edit_text(f"❌ Ocurrió un error al procesar el enlace: <code>{escape_html(str(e))}</code>")

async def handle_music_search(client: Client, message: Message, query: str):
    user = message.from_user
    status_message = await message.reply(f"🔎 Buscando música: <code>{escape_html(query)}</code>...", parse_mode=ParseMode.HTML)
    
    search_results = await asyncio.to_thread(downloader.search_music, query, limit=20)
    
    if not search_results:
        return await status_message.edit_text("❌ No encontré resultados para su búsqueda.")

    search_id = str((await db_instance.search_sessions.insert_one({'user_id': user.id, 'query': query, 'created_at': datetime.utcnow()})).inserted_id)

    docs_to_insert = [{'search_id': search_id, 'user_id': user.id, 'created_at': datetime.utcnow(), **res} for res in search_results]
    if docs_to_insert: await db_instance.search_results.insert_many(docs_to_insert)

    all_results_from_db = await db_instance.search_results.find({"search_id": search_id}).to_list(length=100)
    keyboard = build_search_results_keyboard(all_results_from_db, search_id, page=1)
    
    await status_message.edit_text("✅ He encontrado esto. Seleccione una para descargar:", reply_markup=keyboard, parse_mode=ParseMode.HTML)

@Client.on_message(filters.text & filters.private, group=2)
async def text_gatekeeper_handler(client: Client, message: Message):
    user = message.from_user
    text = message.text.strip()
    
    if text.startswith('/'): return

    if hasattr(client, 'user_data') and client.user_data.get(user.id, {}).get("active_config"):
        return await processing_handler.handle_text_input(client, message)

    user_state = await db_instance.get_user_state(user.id)
    if user_state.get("status") != "idle":
        await message.reply("Estoy esperando que complete una acción anterior. Por favor, use los botones del menú o cancele la operación.")
        return

    url_match = re.search(URL_REGEX, text)
    if url_match:
        return await handle_url_input(client, message, url_match.group(0))

    await handle_music_search(client, message, text)

@Client.on_callback_query(filters.regex(r"^(join_|zip_|batch_|song_|search_|cancel_search_)"))
async def combined_utility_callbacks(client: Client, query: CallbackQuery):
    data = query.data
    if data.startswith("join_"):
        await processing_handler.handle_join_actions(client, query)
    elif data.startswith("zip_"):
        await processing_handler.handle_zip_actions(client, query)
    elif data.startswith("batch_"):
        await processing_handler.handle_batch_actions(client, query)
    elif data.startswith("song_select_"):
        await processing_handler.select_song_from_search(client, query)
    elif data.startswith("search_page_"):
        await processing_handler.handle_search_pagination(client, query)
    elif data.startswith("cancel_search_"):
        await processing_handler.cancel_search_session(client, query)