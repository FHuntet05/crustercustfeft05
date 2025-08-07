import logging
import re
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import (build_processing_menu, build_search_results_keyboard,
                                   build_detailed_format_menu, build_profiles_keyboard,
                                   build_confirmation_keyboard)
from src.helpers.utils import (get_greeting, escape_html, sanitize_filename,
                               format_time, format_view_count, format_upload_date)
from src.core import downloader
from . import processing_handler

logger = logging.getLogger(__name__)

URL_REGEX = r'(https?://[^\s]+)'

def get_config_summary(config: dict) -> str:
    """Genera un resumen legible de la configuración de una tarea."""
    parts = []
    if config.get('transcode'): parts.append(f"📉 {config['transcode'].get('resolution', '...')}")
    if config.get('trim_times'): parts.append("✂️ Trim")
    if config.get('gif_options'): parts.append("🎞️ GIF")
    if config.get('watermark'): parts.append("💧 Watermark")
    if config.get('mute_audio'): parts.append("🔇 Muted")
    if config.get('remove_subtitles'): parts.append("📜 No Subs")
    if config.get('subs_file_id'): parts.append("📜 New Subs")
    if not parts: return "<i>(Default)</i>"
    return ", ".join(parts)

@Client.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    user = message.from_user
    greeting_prefix = get_greeting(user.id)
    await db_instance.get_user_settings(user.id)
    start_message = (
        f"A sus órdenes, {greeting_prefix}bienvenido a la <b>Suite de Medios v2.0</b>.\n\n"
        "He rediseñado mi flujo de trabajo para mayor potencia y claridad.\n\n"
        "<b>Nuevos Comandos:</b>\n"
        "• /panel - Muestra su mesa de trabajo.\n"
        "• /p <code>[ID]</code> - Abre el menú de una tarea del panel.\n"
        "• /profiles - Gestiona sus perfiles de configuración.\n\n"
        "Envíe un archivo o un enlace para comenzar."
    )
    await message.reply(start_message, parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("panel"))
async def panel_command(client: Client, message: Message):
    user = message.from_user
    greeting_prefix = get_greeting(user.id)
    pending_tasks = await db_instance.get_pending_tasks(user.id)
    
    if not pending_tasks:
        text = f"✅ ¡{greeting_prefix}Su mesa de trabajo está vacía!"
        return await message.reply(text, parse_mode=ParseMode.HTML)
    
    response_lines = [f"📋 <b>{greeting_prefix}Su mesa de trabajo actual:</b>\n"]
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

    response_lines.append(f"Use /p <code>[ID]</code> para configurar una tarea (ej: <code>/p {len(pending_tasks)}</code>).")
    response_lines.append(f"Use /p clean para limpiar todas las tareas.")
    await message.reply("\n".join(response_lines), parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("p"))
async def process_command(client: Client, message: Message):
    user = message.from_user
    parts = message.text.split()
    if len(parts) < 2:
        return await message.reply("Uso: `/p [ID]` o `/p clean`.", parse_mode=ParseMode.MARKDOWN)
    
    action = parts[1]
    if action.lower() == "clean":
        return await message.reply(
            "¿Seguro que desea eliminar TODAS las tareas de su panel?",
            reply_markup=build_confirmation_keyboard("panel_delete_all_confirm", "panel_delete_all_cancel")
        )

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

@Client.on_message(filters.command(["profiles", "pr"]))
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

@Client.on_message(filters.command("pr_delete"))
async def pr_delete_command(client: Client, message: Message):
    # Lógica de eliminación se manejará en processing_handler para confirmación
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply("Uso: `/pr_delete [Nombre del Perfil]`")
    
    preset_name = parts[1].lower()
    await message.reply(
        f"¿Seguro que desea eliminar el perfil '<b>{escape_html(preset_name.capitalize())}</b>'?",
        reply_markup=build_confirmation_keyboard(f"profile_delete_confirm_{preset_name}", "profile_delete_cancel"),
        parse_mode=ParseMode.HTML
    )

@Client.on_message(filters.media)
async def any_file_handler(client: Client, message: Message):
    user = message.from_user
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
            await message.reply(f"✅ Archivo recibido y añadido al panel.\nUse `/p 1` (o el ID correspondiente) para configurarlo.")
    else:
        await message.reply(f"❌ Hubo un error al registrar la tarea en la base de datos.")

@Client.on_message(filters.text)
async def text_handler(client: Client, message: Message):
    user = message.from_user
    text = message.text.strip()
    
    if text.startswith('/'): return # Ignorar otros comandos, ya tienen sus manejadores

    url_match = re.search(URL_REGEX, text)
    if url_match:
        url = url_match.group(0)
        status_message = await message.reply(f"🔎 Analizando enlace...", parse_mode=ParseMode.HTML)
        
        info = await downloader.get_url_info(url)
        if not info or not info.get('formats'):
            return await status_message.edit_text("❌ No pude obtener información de ese enlace.")

        task_id = await db_instance.add_task(
            user_id=user.id, file_type='video' if info['is_video'] else 'audio',
            url=info['url'], file_name=sanitize_filename(info['title']), url_info=info
        )
        if not task_id: return await status_message.edit_text("❌ Error al crear la tarea en la DB.")
        
        caption_parts = [
            f"<b>📝 Nombre:</b> {escape_html(info['title'])}",
            f"<b>🕓 Duración:</b> {format_time(info.get('duration'))}",
            f"<b>📢 Canal:</b> {escape_html(info.get('uploader'))}"
        ]
        caption_parts.append("\nElija la calidad del video para la descarga:")
        caption = "\n".join(caption_parts)
        
        keyboard = build_detailed_format_menu(str(task_id), info['formats'])
        
        if info.get('thumbnail'):
            await client.send_photo(chat_id=user.id, photo=info['thumbnail'], caption=caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        else:
            await client.send_message(chat_id=user.id, text=caption, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        
        return await status_message.delete()

    if hasattr(client, 'user_data') and user.id in client.user_data and 'active_config' in client.user_data[user.id]:
        return await processing_handler.handle_text_input_for_config(client, message)

    query = text
    status_message = await message.reply(f"🔎 Buscando <code>{escape_html(query)}</code>...", parse_mode=ParseMode.HTML)
    search_results = await downloader.search_music(query, limit=20)
    
    if not search_results:
        return await status_message.edit_text("❌ No encontré resultados para su búsqueda.")

    search_id = str((await db_instance.search_sessions.insert_one({'user_id': user.id, 'query': query, 'created_at': datetime.utcnow()})).inserted_id)

    docs_to_insert = [{'search_id': search_id, 'user_id': user.id, 'created_at': datetime.utcnow(), **res} for res in search_results]
    if docs_to_insert: await db_instance.search_results.insert_many(docs_to_insert)

    all_results_from_db = await db_instance.search_results.find({"search_id": search_id}).to_list(length=100)
    keyboard = build_search_results_keyboard(all_results_from_db, search_id, page=1)
    
    await status_message.edit_text("✅ He encontrado esto. Seleccione una para descargar:", reply_markup=keyboard, parse_mode=ParseMode.HTML)