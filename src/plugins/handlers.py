# src/plugins/handlers.py

import logging
import re
from datetime import datetime
import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from pyrogram.enums import ParseMode
from bson.objectid import ObjectId

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import (build_processing_menu, build_search_results_keyboard,
                                   build_detailed_format_menu, build_profiles_keyboard,
                                   build_confirmation_keyboard, build_batch_profiles_keyboard,
                                   build_join_selection_keyboard, build_zip_selection_keyboard)
from src.helpers.utils import (get_greeting, escape_html, sanitize_filename,
                               format_time, format_view_count, format_upload_date)
from src.core import downloader
from . import processing_handler # Importamos el módulo completo

logger = logging.getLogger(__name__)

URL_REGEX = r'(https?://[^\s]+)'

def get_config_summary(config: dict) -> str:
    """Genera un resumen legible de la configuración de una tarea."""
    parts = []
    if config.get('transcode'): parts.append(f"📉 {config['transcode'].get('resolution', '...')}")
    if config.get('trim_times'): parts.append("✂️ Cortado")
    if config.get('gif_options'): parts.append("🎞️ GIF")
    if config.get('watermark'): parts.append("💧 Watermark")
    if config.get('mute_audio'): parts.append("🔇 Muted")
    if config.get('remove_subtitles'): parts.append("📜 No Subs")
    if config.get('subs_file_id'): parts.append("📜 New Subs")
    if config.get('remove_thumbnail'): parts.append("🖼️ No Thumb")
    if config.get('extract_thumbnail'): parts.append("🖼️ Extract Thumb")
    if config.get('thumbnail_file_id'): parts.append("🖼️ New Thumb")
    if config.get('extract_audio'): parts.append(f"🎵 Audio extraído")
    if config.get('replace_audio_file_id'): parts.append(f"🎼 Audio reemplazado")
    if not parts: return "<i>(Default)</i>"
    return ", ".join(parts)

@Client.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    user = message.from_user
    greeting_prefix = get_greeting(user.id)
    await db_instance.get_user_settings(user.id)
    start_message = (
        f"A sus órdenes, {greeting_prefix}, bienvenido a la <b>Suite de Medios v2.6</b>.\n\n"
        "He corregido y mejorado los flujos de trabajo.\n\n"
        "<b>Comandos Principales:</b>\n"
        "• /panel - Muestra su mesa de trabajo.\n"
        "• /p <code>[ID]</code> - Abre el menú de una tarea.\n"
        "• /p clean <code>[ID]</code> - Elimina una tarea específica.\n"
        "• /join - Une videos del panel.\n"
        "• /zip - Comprime tareas del panel en un ZIP.\n"
        "• /p_all - Procesa todas las tareas del panel a la vez.\n"
        "• /profiles - Gestiona sus perfiles.\n\n"
        "Envíe un archivo, una búsqueda o un enlace para comenzar."
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
    
    response_lines = [f"📋 <b>{greeting_prefix}, su mesa de trabajo actual:</b>\n"]
    for i, task in enumerate(pending_tasks):
        idx = i + 1
        file_type = task.get('file_type', 'document')
        emoji_map = {'video': '🎬', 'audio': '🎵', 'document': '📄'}
        emoji = emoji_map.get(file_type, '📁')
        display_name = task.get('original_filename') or task.get('url', 'Tarea de URL')
        short_name = (display_name[:50] + '...') if len(display_name) > 53 else display_name
        config_summary = get_config_summary(task.get('processing_config', {}))
        
        response_lines.append(f"<b>{idx}.</b> {emoji} <code>{escape_html(short_name)}</code>")
        response_lines.append(f"   └ ⚙️ {config_summary}\n")

    response_lines.append(f"Use /p <code>[ID]</code> para configurar una tarea (ej: <code>/p 1</code>).")
    response_lines.append(f"Use /join para unir videos del panel.")
    response_lines.append(f"Use /zip para comprimir tareas.")
    response_lines.append(f"Use /p_all para procesar todo el panel.")
    response_lines.append(f"Use /p clean para limpiar todas las tareas.")
    await message.reply("\n".join(response_lines), parse_mode=ParseMode.HTML)

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
        task_id = str(task['_id'])
        filename = task.get('original_filename', '...')
        keyboard = build_processing_menu(task_id, task['file_type'], task)
        await message.reply(f"🛠️ Configurando Tarea <b>#{task_index+1}</b>:\n<code>{escape_html(filename)}</code>", reply_markup=keyboard, parse_mode=ParseMode.HTML)
    else:
        await message.reply(f"❌ ID inválido. Tiene {len(pending_tasks)} tareas en su panel. Use un número entre 1 y {len(pending_tasks)}.")

@Client.on_message(filters.command("p_all") & filters.private)
async def process_all_command(client: Client, message: Message):
    user = message.from_user
    pending_tasks_count = await db_instance.tasks.count_documents({"user_id": user.id, "status": "pending_processing"})

    if pending_tasks_count == 0:
        return await message.reply("✅ Su panel ya está vacío. No hay nada que procesar.")

    user_presets = await db_instance.get_user_presets(user.id)
    keyboard = build_batch_profiles_keyboard(user_presets)
    await message.reply(
        f" va a procesar <b>{pending_tasks_count}</b> tareas.\n\n"
        "Seleccione un perfil para aplicar a todas las tareas o use la configuración por defecto.",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@Client.on_message(filters.command("join") & filters.private)
async def join_videos_command(client: Client, message: Message):
    user = message.from_user
    video_tasks = await db_instance.tasks.find({
        "user_id": user.id, "status": "pending_processing", "file_type": "video"
    }).to_list(length=100)

    if len(video_tasks) < 2:
        return await message.reply("Necesita al menos 2 videos en su panel para unirlos.")

    if not hasattr(client, 'user_data'): client.user_data = {}
    client.user_data[user.id] = { "join_mode": { "available_tasks": video_tasks, "selected_ids": [] } }

    keyboard = build_join_selection_keyboard(video_tasks, [])
    await message.reply(
        "🎬 <b>Modo de Unión de Videos</b>\n\nSeleccione los videos que desea unir en el orden deseado.",
        reply_markup=keyboard, parse_mode=ParseMode.HTML
    )

@Client.on_message(filters.command("zip") & filters.private)
async def zip_files_command(client: Client, message: Message):
    user = message.from_user
    all_tasks = await db_instance.get_pending_tasks(user.id)

    if not all_tasks:
        return await message.reply("No hay tareas en su panel para comprimir.")

    if not hasattr(client, 'user_data'): client.user_data = {}
    client.user_data[user.id] = { "zip_mode": { "available_tasks": all_tasks, "selected_ids": [] } }

    keyboard = build_zip_selection_keyboard(all_tasks, [])
    await message.reply(
        "📦 <b>Modo de Compresión ZIP</b>\n\nSeleccione las tareas que desea añadir al archivo ZIP.",
        reply_markup=keyboard, parse_mode=ParseMode.HTML
    )


@Client.on_message(filters.command(["profiles", "pr"]) & filters.private)
async def profiles_command(client: Client, message: Message):
    user = message.from_user
    presets = await db_instance.get_user_presets(user.id)
    
    if not presets:
        text = "No tiene perfiles guardados. Para crear uno:\n1. Configure una tarea con `/p [ID]`.\n2. Pulse 'Guardar como Perfil'."
        return await message.reply(text)

    response_lines = ["💾 <b>Sus Perfiles Guardados:</b>\n"]
    for preset in presets:
        preset_name = preset.get('preset_name', 'N/A').capitalize()
        config_summary = get_config_summary(preset.get('config_data', {}))
        response_lines.append(f"• <b>{preset_name}</b>: {config_summary}")
    
    response_lines.append("\nUse `/pr_delete [Nombre]` para eliminar un perfil.")
    await message.reply("\n".join(response_lines), parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("pr_delete") & filters.private)
async def pr_delete_command(client: Client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply("Uso: `/pr_delete [Nombre del Perfil]`")
    
    preset_name = parts[1].lower()
    await message.reply(
        f"¿Seguro que desea eliminar el perfil '<b>{escape_html(preset_name.capitalize())}</b>'?",
        reply_markup=build_confirmation_keyboard(f"profile_delete_confirm_{preset_name}", "profile_delete_cancel"),
        parse_mode=ParseMode.HTML
    )

# --- ARQUITECTURA CORREGIDA: GATEKEEPER PARA MEDIA ---
@Client.on_message(filters.media & filters.private, group=1)
async def any_file_handler(client: Client, message: Message):
    user = message.from_user
    # Este manejador solo debe actuar si el usuario NO está en un modo de configuración activa.
    # Si lo está, detenemos la propagación para permitir que el manejador de `processing_handler` actúe.
    if hasattr(client, 'user_data') and user.id in client.user_data and client.user_data[user.id].get("active_config"):
        message.stop_propagation()
        return

    original_media_object, file_type = None, None
    if message.video: original_media_object, file_type = message.video, 'video'
    elif message.audio: original_media_object, file_type = message.audio, 'audio'
    elif message.document: original_media_object, file_type = message.document, 'document'
    
    if not original_media_object: return

    final_file_name = sanitize_filename(getattr(original_media_object, 'file_name', "Archivo Sin Nombre"))
    
    task_id = await db_instance.add_task(
        user_id=user.id, file_type=file_type, file_name=final_file_name,
        file_id=original_media_object.file_id, file_size=original_media_object.file_size
    )

    if task_id:
        user_presets = await db_instance.get_user_presets(user.id)
        if user_presets:
            keyboard = build_profiles_keyboard(str(task_id), user_presets)
            await message.reply("✅ Archivo recibido y añadido al panel. ¿Desea aplicar un perfil?", reply_markup=keyboard)
        else:
            count = await db_instance.tasks.count_documents({'user_id': user.id, 'status': 'pending_processing'})
            await message.reply(f"✅ Archivo recibido y añadido al panel.\nUse `/p {count}` para configurarlo.")
    else:
        await message.reply(f"❌ Hubo un error al registrar la tarea en la base de datos.")

async def handle_url_input(client: Client, message: Message, url: str):
    user = message.from_user
    status_message = await message.reply(f"🔎 Analizando enlace...", parse_mode=ParseMode.HTML)
    
    try:
        info = await asyncio.to_thread(downloader.get_url_info, url)
        if not info or not info.get('formats'):
            return await status_message.edit_text("❌ No pude obtener información de ese enlace.")

        task_id = await db_instance.add_task(
            user_id=user.id, file_type='video' if info['is_video'] else 'audio',
            url=info['url'], file_name=sanitize_filename(info['title']), url_info=info
        )
        if not task_id: return await status_message.edit_text("❌ Error al crear la tarea en la DB.")
        
        caption_parts = [ f"<b>📝 Nombre:</b> {escape_html(info['title'])}", f"<b>🕓 Duración:</b> {format_time(info.get('duration'))}", f"<b>📢 Canal:</b> {escape_html(info.get('uploader'))}" ]
        caption_parts.append("\nElija la calidad para la descarga:")
        caption = "\n".join(caption_parts)
        
        keyboard = build_detailed_format_menu(str(task_id), info['formats'])
        
        await status_message.delete()
        
        if info.get('thumbnail'):
            await client.send_photo(chat_id=user.id, photo=info['thumbnail'], caption=caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        else:
            await client.send_message(chat_id=user.id, text=caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Error procesando URL {url}: {e}")
        await status_message.edit_text(f"❌ Ocurrió un error al procesar el enlace: <code>{escape_html(str(e))}</code>")

async def handle_music_search(client: Client, message: Message, query: str):
    user = message.from_user
    status_message = await message.reply(f"🔎 Buscando <code>{escape_html(query)}</code>...", parse_mode=ParseMode.HTML)
    
    search_results = await asyncio.to_thread(downloader.search_music, query, limit=20)
    
    if not search_results:
        return await status_message.edit_text("❌ No encontré resultados para su búsqueda.")

    search_id = str((await db_instance.search_sessions.insert_one({'user_id': user.id, 'query': query, 'created_at': datetime.utcnow()})).inserted_id)

    docs_to_insert = [{'search_id': search_id, 'user_id': user.id, 'created_at': datetime.utcnow(), **res} for res in search_results]
    if docs_to_insert: await db_instance.search_results.insert_many(docs_to_insert)

    all_results_from_db = await db_instance.search_results.find({"search_id": search_id}).to_list(length=100)
    keyboard = build_search_results_keyboard(all_results_from_db, search_id, page=1)
    
    await status_message.edit_text("✅ He encontrado esto. Seleccione una para descargar:", reply_markup=keyboard, parse_mode=ParseMode.HTML)

@Client.on_message(filters.text & filters.private)
async def text_gatekeeper_handler(client: Client, message: Message):
    user = message.from_user
    text = message.text.strip()
    
    if text.startswith('/'):
        return

    if hasattr(client, 'user_data') and user.id in client.user_data and client.user_data[user.id].get("active_config"):
        return await processing_handler.handle_text_input(client, message)

    url_match = re.search(URL_REGEX, text)
    if url_match:
        return await handle_url_input(client, message, url_match.group(0))

    await handle_music_search(client, message, text)