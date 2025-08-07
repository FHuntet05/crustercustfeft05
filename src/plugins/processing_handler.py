# --- START OF FILE src/plugins/processing_handler.py ---

import logging
import asyncio
import re  # Añadido para validación de formato
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from pyrogram.enums import ParseMode
from bson.objectid import ObjectId

from src.db.mongo_manager import db_instance
from src.core import downloader
from src.helpers.keyboards import (build_back_button, build_processing_menu, 
                                   build_quality_menu, build_download_quality_menu, 
                                   build_audio_convert_menu, build_audio_effects_menu,
                                   build_search_results_keyboard, build_panel_keyboard)
from src.helpers.utils import get_greeting, escape_html, sanitize_filename

logger = logging.getLogger(__name__)

# --- MANEJADORES DE CALLBACKS DE BOTONES ---

@Client.on_callback_query(filters.regex(r"^panel_"))
async def on_panel_action(client: Client, query: CallbackQuery):
    await query.answer()
    user = query.from_user
    action = query.data.split("_")[1]

    if action == "delete_all":
        count = (await db_instance.tasks.delete_many({"user_id": user.id, "status": "pending_processing"})).deleted_count
        await query.message.edit_text(f"💥 Limpieza completada. Se descartaron {count} tareas.")
    elif action == "show":
        greeting_prefix = get_greeting(user.id)
        pending_tasks = await db_instance.get_pending_tasks(user.id)
        if not pending_tasks:
            text = f"✅ ¡{greeting_prefix}Su mesa de trabajo está vacía!"
            return await query.message.edit_text(text)
        
        keyboard = build_panel_keyboard(pending_tasks)
        response_text = f"📋 <b>{greeting_prefix}Su mesa de trabajo actual:</b>"
        await query.message.edit_text(response_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@Client.on_callback_query(filters.regex(r"^task_"))
async def on_task_action(client: Client, query: CallbackQuery):
    await query.answer()
    parts = query.data.split("_")
    action, task_id = parts[1], "_".join(parts[2:])
    
    task = await db_instance.get_task(task_id)
    if not task:
        return await query.message.edit_text("❌ Error: La tarea ya no existe.", reply_markup=None)

    if action == "process":
        filename = task.get('original_filename', '...')
        keyboard = build_processing_menu(task_id, task['file_type'], task, filename)
        await query.message.edit_text(f"🛠️ ¿Qué desea hacer con:\n<code>{escape_html(filename)}</code>?", reply_markup=keyboard, parse_mode=ParseMode.HTML)
    
    elif action == "queuesingle":
        await db_instance.update_task(task_id, "status", "queued")
        await query.message.edit_text("🔥 Tarea enviada a la forja. El procesamiento comenzará en breve.")

@Client.on_callback_query(filters.regex(r"^config_"))
async def show_config_menu(client: Client, query: CallbackQuery):
    await query.answer()
    
    parts = query.data.split("_")
    menu_type, task_id = parts[1], "_".join(parts[2:])
    
    task = await db_instance.get_task(task_id)
    if not task:
        return await query.message.edit_text("❌ Error: Tarea no encontrada.")

    if menu_type == "dlquality":
        url_info = task.get('url_info')
        if not url_info or not url_info.get('formats'):
            return await query.message.edit_text("❌ No hay información de formatos para esta tarea.")
        keyboard = build_download_quality_menu(task_id, url_info['formats'])
        return await query.message.edit_text("💿 Seleccione la calidad a descargar:", reply_markup=keyboard)

    if menu_type == "quality": return await query.message.edit_text("⚙️ Seleccione el perfil de calidad:", reply_markup=build_quality_menu(task_id))
    if menu_type == "audioconvert": return await query.message.edit_text("🔊 Configure la conversión de audio:", reply_markup=build_audio_convert_menu(task_id))
    if menu_type == "audioeffects":
        keyboard = build_audio_effects_menu(task_id, task.get('processing_config', {}))
        return await query.message.edit_text("🎧 Aplique efectos de audio:", reply_markup=keyboard)

    original_filename = task.get('original_filename', 'archivo')
    greeting_prefix = get_greeting(query.from_user.id)
    
    if not hasattr(client, 'user_data'): client.user_data = {}
    client.user_data[query.from_user.id] = {"active_config": {"task_id": task_id, "menu_type": menu_type}}

    menu_texts = {
        "rename": f"✏️ <b>Renombrar Archivo</b>\n\n{greeting_prefix}envíeme el nuevo nombre para <code>{escape_html(original_filename)}</code>.\n<i>No incluya la extensión.</i>",
        "trim": f"✂️ <b>Cortar</b>\n\n{greeting_prefix}envíeme el tiempo de inicio y fin.\nFormatos: <code>HH:MM:SS-HH:MM:SS</code> o <code>MM:SS-MM:SS</code>.",
        "split": f"🧩 <b>Dividir Video</b>\n\n{greeting_prefix}envíeme el criterio de división por tiempo (ej. <code>300s</code>).",
        "gif": f"🎞️ <b>Crear GIF</b>\n\n{greeting_prefix}envíeme la duración y los FPS.\nFormato: <code>[duración] [fps]</code> (ej: <code>5 15</code>).",
        "audiotags": f"🖼️ <b>Editar Tags</b>\n\n{greeting_prefix}envíeme los nuevos metadatos.\nFormato: <code>Nuevo Título - Nuevo Artista</code>.",
    }
    
    text = menu_texts.get(menu_type, "Configuración no reconocida.")
    back_button_cb = f"task_process_{task_id}"
    keyboard = build_back_button(back_button_cb)
    await query.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@Client.on_callback_query(filters.regex(r"^set_"))
async def set_value_callback(client: Client, query: CallbackQuery):
    await query.answer()
    parts = query.data.split("_")
    config_type, task_id, value = parts[1], parts[2], "_".join(parts[3:])

    task = await db_instance.get_task(task_id)
    if not task: return await query.message.edit_text("❌ Error: Tarea no encontrada.")

    if config_type == "dlformat":
        update_result = await db_instance.tasks.update_one(
            {"_id": ObjectId(task_id)},
            {"$set": {
                "processing_config.download_format_id": value,
                "status": "queued"
            }}
        )
        if update_result.modified_count > 0:
            await query.message.edit_text("✅ Formato seleccionado.\n\n🔥 Tarea enviada a la forja.", parse_mode=ParseMode.HTML)
        else:
            await query.message.edit_text("❌ Error al encolar la tarea. Por favor, inténtelo de nuevo.")
        return

    elif config_type == "quality": await db_instance.update_task_config(task_id, "quality", value)
    elif config_type == "mute": current = task.get('processing_config', {}).get('mute_audio', False); await db_instance.update_task_config(task_id, "mute_audio", not current)
    elif config_type == "audioprop": prop_key, prop_value = parts[3], parts[4]; await db_instance.update_task_config(task_id, f"audio_{prop_key}", prop_value)
    elif config_type == "audioeffect": 
        effect = parts[3]
        current = task.get('processing_config', {}).get(effect, False)
        await db_instance.update_task_config(task_id, effect, not current)
        task = await db_instance.get_task(task_id)
        keyboard = build_audio_effects_menu(task_id, task.get('processing_config', {}))
        return await query.message.edit_text("🎧 Efectos de audio actualizados:", reply_markup=keyboard)

    task = await db_instance.get_task(task_id)
    keyboard = build_processing_menu(task_id, task['file_type'], task, task.get('original_filename', ''))
    await query.message.edit_text(
        f"🛠️ Configuración actualizada.",
        reply_markup=keyboard, parse_mode=ParseMode.HTML
    )

@Client.on_callback_query(filters.regex(r"^song_select_"))
async def on_song_select(client: Client, query: CallbackQuery):
    result_id = query.data.split("_")[2]
    user = query.from_user

    search_result = await db_instance.search_results.find_one_and_delete({"_id": ObjectId(result_id), "user_id": user.id})
    if not search_result:
        await query.answer("Esta búsqueda ha expirado.", show_alert=True)
        return await query.message.delete()
    
    # Limpiar el resto de los resultados de esta búsqueda
    if search_id := search_result.get('search_id'):
        await db_instance.search_results.delete_many({"search_id": search_id})
        await db_instance.search_sessions.delete_one({"_id": ObjectId(search_id)})

    # ---- NUEVO FLUJO INSTANTÁNEO ----
    # 1. Obtener la URL (o el término para buscarla) del resultado.
    search_term_or_url = search_result.get('url') or f"ytsearch1:{search_result.get('search_term')}"
    title = search_result.get('title', 'Canción Desconocida')
    
    # 2. Crear una tarea "ligera" que el worker se encargará de procesar.
    processing_config = {
        "task_type": "audio_search" # Nueva bandera para el worker
    }
    
    task_id = await db_instance.add_task(
        user_id=user.id,
        file_type='audio', # Asumimos audio por defecto
        url=search_term_or_url, # El worker resolverá esto a una URL concreta si es necesario
        file_name=sanitize_filename(title), 
        processing_config=processing_config,
        status="queued" # Encolar directamente
    )

    if not task_id:
        return await query.message.edit_text("❌ Error al crear la tarea en la DB.")
    
    # 3. Responder inmediatamente al usuario.
    await query.message.edit_text(
        f"🔥 <b>{escape_html(title)}</b>\n\nTu canción ha sido enviada a la forja.", 
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    await query.answer()

@Client.on_callback_query(filters.regex(r"^search_page_"))
async def on_search_page(client: Client, query: CallbackQuery):
    await query.answer()
    parts = query.data.split("_")
    search_id, page = parts[2], int(parts[3])

    session = await db_instance.search_sessions.find_one({"_id": ObjectId(search_id)})
    if not session: return await query.message.edit_text("❌ Esta sesión de búsqueda ha expirado.")

    all_results = await db_instance.search_results.find({"search_id": search_id}).sort("created_at", 1).to_list(length=100)
    if not all_results: return await query.message.edit_text("❌ No se encontraron resultados para esta sesión.")

    keyboard = build_search_results_keyboard(all_results, search_id, page)
    await query.message.edit_text(
        f"✅ Resultados para: <b>{escape_html(session['query'])}</b>",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

@Client.on_callback_query(filters.regex(r"^cancel_search_"))
async def on_cancel_search(client: Client, query: CallbackQuery):
    await query.answer()
    search_id = query.data.split("_")[2]
    await db_instance.search_results.delete_many({"search_id": search_id})
    await db_instance.search_sessions.delete_one({"_id": ObjectId(search_id)})
    await query.message.edit_text("✅ Búsqueda cancelada y resultados limpiados.")

@Client.on_callback_query(filters.regex(r"^noop"))
async def noop_callback(client: Client, query: CallbackQuery):
    await query.answer()

async def handle_text_input_for_config(client: Client, message: Message):
    user_id = message.from_user.id
    user_input = message.text.strip()
    
    active_config_data = client.user_data.get(user_id, {}).get('active_config')
    if not active_config_data: 
        return

    task_id, menu_type = active_config_data['task_id'], active_config_data['menu_type']
    del client.user_data[user_id]['active_config']
    
    feedback_message = "✅ Configuración guardada."
    try:
        if menu_type == "rename":
            await db_instance.update_task_config(task_id, "final_filename", user_input)
            feedback_message = f"✅ Nombre actualizado a <code>{escape_html(user_input)}</code>."
        
        elif menu_type == "trim":
            # --- NUEVA LÓGICA DE CORTE (TRIM) ---
            time_regex = re.compile(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*$")
            if not time_regex.match(user_input):
                feedback_message = "❌ <b>Formato inválido.</b>\nPor favor, use <code>MM:SS-MM:SS</code> o <code>HH:MM:SS-HH:MM:SS</code>."
            else:
                await db_instance.update_task_config(task_id, "trim_times", user_input)
                feedback_message = f"✅ Tiempos de corte guardados: <code>{escape_html(user_input)}</code>."
            # --- FIN DE LA LÓGICA ---

        elif menu_type == "split":
            await db_instance.update_task_config(task_id, "split_criteria", user_input)
            feedback_message = f"✅ Criterio de división guardado: <code>{escape_html(user_input)}</code>."
        elif menu_type == "gif":
            duration, fps = user_input.split()
            await db_instance.update_task_config(task_id, "gif_options", {"duration": duration, "fps": fps})
            feedback_message = f"✅ GIF se creará con {duration}s a {fps}fps."
        elif menu_type == "audiotags":
            title, artist = [part.strip() for part in user_input.split('-', 1)]
            await db_instance.update_task_config(task_id, "audio_tags", {"title": title, "artist": artist})
            feedback_message = f"✅ Tags actualizados: Título: {title}, Artista: {artist}."

    except Exception as e:
        logger.error(f"Error al procesar entrada de texto para config: {e}")
        feedback_message = "❌ Formato incorrecto o error al guardar."

    await message.reply(feedback_message, parse_mode=ParseMode.HTML, quote=True)
    
    task = await db_instance.get_task(task_id)
    if task:
        keyboard = build_processing_menu(task_id, task['file_type'], task, task.get('original_filename', ''))
        await message.reply("¿Algo más?", reply_markup=keyboard, parse_mode=ParseMode.HTML)
# --- END OF FILE src/plugins/processing_handler.py ---