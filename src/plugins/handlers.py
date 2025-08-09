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
                                   build_join_selection_keyboard, build_zip_selection_keyboard,
                                   build_profiles_management_keyboard)
from src.helpers.utils import (get_greeting, escape_html, sanitize_filename,
                               format_time, format_task_details_rich)
from src.core import downloader
from src.core.exceptions import AuthenticationError
from . import processing_handler

logger = logging.getLogger(__name__)

URL_REGEX = r'(https?://[^\s]+)'
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

@Client.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    await reset_user_state_if_needed(client, message.from_user.id)
    greeting = get_greeting(message.from_user.id)
    start_message = (
        f"A sus órdenes, {greeting}, bienvenido a la <b>Suite de Medios v15.1 (Final)</b>.\n\n"
        "Sistema de estado reiniciado y listo para nuevas tareas.\n\n"
        "<b>Comandos Principales:</b>\n"
        "• /panel - Muestra su mesa de trabajo con detalles.\n"
        "• /p <code>[ID]</code> - Abre el menú de una tarea.\n"
        "• /p clean - Limpia todas las tareas del panel.\n"
        "• /profiles - Gestiona sus perfiles de configuración.\n"
        "• /join - Une varios videos del panel.\n"
        "• /zip - Comprime varias tareas en un archivo ZIP.\n"
        "• /p_all - Procesa todas las tareas del panel.\n\n"
        "Envíe un archivo, enlace de YouTube o un texto para buscar música."
    )
    await message.reply(start_message, parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("panel") & filters.private)
async def panel_command(client: Client, message: Message):
    user_id = message.from_user.id
    greeting = get_greeting(user_id).replace(',', '')
    pending_tasks = await db_instance.get_pending_tasks(user_id)
    
    if not pending_tasks:
        return await message.reply(f"✅ ¡{greeting}, su mesa de trabajo está vacía!")
    
    response = [f"📋 <b>{greeting}, su mesa de trabajo actual:</b>"]
    for i, task in enumerate(pending_tasks):
        response.append(format_task_details_rich(task, i + 1))
    response.extend([f"\nUse /p <code>[ID]</code> para configurar.", f"Use /p clean para limpiar todo."])
    await message.reply("\n".join(response), parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@Client.on_message(filters.command("p") & filters.private)
async def process_command(client: Client, message: Message):
    user_id = message.from_user.id
    parts = message.text.split()
    if len(parts) < 2:
        return await message.reply("Uso: `/p [ID]` o `/p clean`.", parse_mode=ParseMode.MARKDOWN)
    
    action = parts[1].lower()
    if action == "clean":
        return await message.reply("¿Seguro que desea eliminar TODAS las tareas?", reply_markup=build_confirmation_keyboard("panel_delete_all_confirm", "panel_delete_all_cancel"))

    if not action.isdigit():
        return await message.reply("El ID debe ser un número. Use `/panel`.")
        
    task_index = int(action) - 1
    pending_tasks = await db_instance.get_pending_tasks(user_id)
    if 0 <= task_index < len(pending_tasks):
        await processing_handler.open_task_menu_from_p(client, message, str(pending_tasks[task_index]['_id']))
    else:
        await message.reply(f"❌ ID inválido. Tiene {len(pending_tasks)} tareas.")

@Client.on_message(filters.command("profiles") & filters.private)
async def profiles_command(client: Client, message: Message):
    user_id = message.from_user.id
    presets = await db_instance.get_user_presets(user_id)
    await message.reply("<b>Gestión de Perfiles:</b>\nAquí puede eliminar perfiles guardados.",
                        reply_markup=build_profiles_management_keyboard(presets), parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("join") & filters.private)
async def join_command(client: Client, message: Message):
    user_id = message.from_user.id
    video_tasks = await db_instance.get_pending_tasks(user_id, file_type_filter="video")
    if len(video_tasks) < 2:
        return await message.reply("❌ Necesita al menos 2 videos en su panel para usar /join.")
    
    await db_instance.set_user_state(user_id, "selecting_join_files", data={"selected_ids": []})
    await message.reply("🎬 <b>Modo de Unión de Videos</b>\nSeleccione los videos que desea unir en orden:",
                        reply_markup=build_join_selection_keyboard(video_tasks, []), parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("zip") & filters.private)
async def zip_command(client: Client, message: Message):
    user_id = message.from_user.id
    tasks = await db_instance.get_pending_tasks(user_id)
    if not tasks:
        return await message.reply("❌ Su panel está vacío. No hay nada que comprimir.")
    
    await db_instance.set_user_state(user_id, "selecting_zip_files", data={"selected_ids": []})
    await message.reply("📦 <b>Modo de Compresión ZIP</b>\nSeleccione las tareas que desea incluir en el archivo .zip:",
                        reply_markup=build_zip_selection_keyboard(tasks, []), parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("p_all") & filters.private)
async def process_all_command(client: Client, message: Message):
    user_id = message.from_user.id
    tasks = await db_instance.get_pending_tasks(user_id)
    if not tasks:
        return await message.reply("❌ No hay tareas pendientes para procesar.")
    
    presets = await db_instance.get_user_presets(user_id)
    await message.reply(f"Va a procesar en lote <b>{len(tasks)}</b> tareas.\nSeleccione un perfil para aplicar a todas:",
                        reply_markup=build_batch_profiles_keyboard(presets), parse_mode=ParseMode.HTML)

async def reset_user_state_if_needed(client: Client, user_id: int):
    user_state = await db_instance.get_user_state(user_id)
    if user_state.get("status") != "idle":
        logger.warning(f"Reseteando estado obsoleto '{user_state.get('status')}' para {user_id}.")
        if source_id := user_state.get("data", {}).get("source_message_id"):
            try: await client.delete_messages(user_id, source_id)
            except Exception: pass
        await db_instance.set_user_state(user_id, "idle")

@Client.on_message(filters.media & filters.private, group=1)
async def media_gatekeeper(client: Client, message: Message):
    user_id = message.from_user.id
    user_state = await db_instance.get_user_state(user_id)
    
    if user_state.get("status") != "idle":
        return await processing_handler.handle_media_input_for_state(client, message, user_state)

    media = message.video or message.audio or message.document
    file_type = 'video' if message.video else 'audio' if message.audio else 'document'
    status = "pending_metadata" if file_type == 'document' else "pending_processing"
    
    metadata = {}
    if file_type == 'video': metadata = {"resolution": f"{media.width}x{media.height}" if media.width else None, "duration": media.duration}
    elif file_type == 'audio': metadata = {"duration": media.duration}
    metadata['size'] = media.file_size
    
    task_id = await db_instance.add_task(user_id=user_id, file_type=file_type, file_name=sanitize_filename(getattr(media, 'file_name')), 
                                       file_id=media.file_id, status=status, metadata=metadata)
    if not task_id: return await message.reply("❌ Error al registrar la tarea.")

    count = await db_instance.tasks.count_documents({'user_id': user_id, 'status': 'pending_processing'})
    reply_text = f"✅ Documento en cola para análisis." if status == "pending_metadata" else f"Añadido al panel como tarea <b>#{count}</b>."
    status_msg = await message.reply(reply_text, parse_mode=ParseMode.HTML)
    
    if status == "pending_processing":
        if presets := await db_instance.get_user_presets(user_id):
            await status_msg.edit("¿Desea aplicar un perfil?", reply_markup=build_profiles_keyboard(str(task_id), presets))

@Client.on_message(filters.text & filters.private, group=2)
async def text_gatekeeper(client: Client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    if text.startswith('/'): return

    user_state = await db_instance.get_user_state(user_id)
    if user_state.get("status") != "idle":
        return await processing_handler.handle_text_input_for_state(client, message, user_state)

    if re.search(URL_REGEX, text):
        return await handle_url_input(client, message, text)
    
    await handle_music_search(client, message, text)

async def handle_url_input(client: Client, message: Message, url: str):
    status_msg = await message.reply("🔎 Analizando enlace...")
    try:
        info = await asyncio.to_thread(downloader.get_url_info, url)
        if not info: raise ValueError("No pude obtener información de ese enlace.")
        
        caption = f"<b>📝 Nombre:</b> {escape_html(info['title'])}\n<b>🕓 Duración:</b> {format_time(info.get('duration'))}"
        temp_info_id = str((await db_instance.search_results.insert_one({'user_id': message.from_user.id, 'data': info, 'created_at': datetime.utcnow()})).inserted_id)
        keyboard = build_detailed_format_menu(temp_info_id, info['formats'])
        
        await status_msg.delete()
        if info.get('thumbnail'):
            await client.send_photo(message.from_user.id, photo=info['thumbnail'], caption=caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        else:
            await client.send_message(message.from_user.id, caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error procesando URL {url}: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Ocurrió un error: <code>{escape_html(str(e))}</code>")

async def handle_music_search(client: Client, message: Message, query: str):
    status_msg = await message.reply(f"🔎 Buscando música: <code>{escape_html(query)}</code>...", parse_mode=ParseMode.HTML)
    results = await asyncio.to_thread(downloader.search_music, query, limit=10)
    if not results: return await status_msg.edit_text("❌ No encontré resultados.")

    search_id = str((await db_instance.search_sessions.insert_one({'user_id': message.from_user.id, 'created_at': datetime.utcnow()})).inserted_id)
    docs = [{'search_id': search_id, 'created_at': datetime.utcnow(), **res} for res in results]
    await db_instance.search_results.insert_many(docs)
    await status_msg.edit_text("✅ He encontrado esto. Seleccione una:", reply_markup=build_search_results_keyboard(docs, search_id))

@Client.on_callback_query(filters.regex(r"^set_dlformat_"))
async def set_download_format_callback(client: Client, query: CallbackQuery):
    await query.answer("Preparando tarea...")
    parts = query.data.split("_")
    temp_info_id, format_id = parts[2], "_".join(parts[3:])

    info_doc = await db_instance.search_results.find_one_and_delete({"_id": ObjectId(temp_info_id)})
    if not info_doc: return await query.message.edit_text("❌ Esta selección ha expirado.")
    
    info = info_doc['data']
    file_type = 'audio' if 'audio' in format_id or 'mp3' in format_id else 'video'
    
    task_id = await db_instance.add_task(user_id=query.from_user.id, file_type=file_type, file_name=sanitize_filename(info['title']),
                                       url=info['url'], processing_config={"download_format_id": format_id}, url_info=info, status="queued")

    await query.message.edit_text(f"✅ <b>¡Enviado a la cola!</b>\n🔗 <code>{escape_html(info['title'])}</code>", parse_mode=ParseMode.HTML)