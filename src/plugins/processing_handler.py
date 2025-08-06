# src/plugins/processing_handler.py

import logging
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from pyrogram.enums import ParseMode
from bson.objectid import ObjectId
import asyncio

from src.db.mongo_manager import db_instance
from src.core import downloader
from src.helpers.keyboards import (build_back_button, build_processing_menu, 
                                   build_quality_menu, build_download_quality_menu, 
                                   build_audio_convert_menu, build_audio_effects_menu,
                                   build_search_results_keyboard)
from src.helpers.utils import get_greeting, escape_html, sanitize_filename

logger = logging.getLogger(__name__)

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
    client.user_data[query.from_user.id] = {"task_id": task_id, "menu_type": menu_type}

    menu_texts = {
        "rename": f"✏️ <b>Renombrar Archivo</b>\n\n{greeting_prefix}envíeme el nuevo nombre para <code>{escape_html(original_filename)}</code>.\n<i>No incluya la extensión.</i>",
        "trim": f"✂️ <b>Cortar</b>\n\n{greeting_prefix}envíeme el tiempo de inicio y fin.\nFormatos: <code>HH:MM:SS-HH:MM:SS</code> o <code>MM:SS-MM:SS</code>.",
        "split": f"🧩 <b>Dividir Video</b>\n\n{greeting_prefix}envíeme el criterio de división por tiempo (ej. <code>300s</code>).",
        "gif": f"🎞️ <b>Crear GIF</b>\n\n{greeting_prefix}envíeme la duración y los FPS.\nFormato: <code>[duración] [fps]</code> (ej: <code>5 15</code>).",
    }
    
    text = menu_texts.get(menu_type, "Configuración no reconocida.")
    back_button_cb = f"task_process_{task_id}"
    keyboard = build_back_button(back_button_cb)
    await query.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def handle_text_input_for_config(client: Client, message: Message):
    user_id = message.from_user.id
    user_input = message.text.strip()
    
    active_config = client.user_data.get(user_id)
    if not active_config: return

    task_id, menu_type = active_config['task_id'], active_config['menu_type']
    del client.user_data[user_id]
    
    feedback_message = "✅ Configuración guardada."
    try:
        if menu_type == "rename": await db_instance.update_task_config(task_id, "final_filename", user_input); feedback_message = f"✅ Nombre actualizado a <code>{escape_html(user_input)}</code>."
        elif menu_type == "trim": await db_instance.update_task_config(task_id, "trim_times", user_input); feedback_message = f"✅ Tiempos de corte: <code>{escape_html(user_input)}</code>."
        elif menu_type == "split": await db_instance.update_task_config(task_id, "split_criteria", user_input); feedback_message = f"✅ Criterio de división: <code>{escape_html(user_input)}</code>."
        elif menu_type == "gif":
            duration, fps = user_input.split(); await db_instance.update_task_config(task_id, "gif_options", {"duration": duration, "fps": fps}); feedback_message = f"✅ GIF se creará con {duration}s a {fps}fps."
    except Exception as e: logger.error(f"Error al procesar entrada: {e}"); feedback_message = "❌ Formato incorrecto o error."

    await message.reply(feedback_message, parse_mode=ParseMode.HTML, quote=True)
    
    task = await db_instance.get_task(task_id)
    if task:
        keyboard = build_processing_menu(task_id, task['file_type'], task, task.get('original_filename', ''))
        await message.reply("¿Algo más?", reply_markup=keyboard, parse_mode=ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^set_"))
async def set_value_callback(client: Client, query: CallbackQuery):
    await query.answer()
    parts, value = query.data.split("_"), "_".join(query.data.split("_")[3:])
    config_type, task_id = parts[1], parts[2]

    task = await db_instance.get_task(task_id)
    if not task: return await query.message.edit_text("❌ Error: Tarea no encontrada.")

    if config_type == "dlformat":
        chosen_format_id = value
        url_info = task.get('url_info', {})
        chosen_format = next((f for f in url_info.get('formats', []) if f.get('format_id') == chosen_format_id), None)
        
        if chosen_format and chosen_format.get('vcodec', 'none') == 'none':
            await db_instance.update_task(task_id, 'file_type', 'audio')
            
        await db_instance.update_task_config(task_id, "download_format_id", value)
        await db_instance.update_task(task_id, "status", "queued")
        return await query.message.edit_text(f"✅ Formato <code>{value}</code> seleccionado.\n\n🔥 Tarea enviada a la forja.", parse_mode=ParseMode.HTML)
    
    elif config_type == "quality": await db_instance.update_task_config(task_id, "quality", value)
    elif config_type == "mute": current = task.get('processing_config', {}).get('mute_audio', False); await db_instance.update_task_config(task_id, "mute_audio", not current)
    elif config_type == "audioprop": prop_key, prop_value = parts[3], parts[4]; await db_instance.update_task_config(task_id, f"audio_{prop_key}", prop_value)
    elif config_type == "audioeffect": effect = parts[3]; current = task.get('processing_config', {}).get(effect, False); await db_instance.update_task_config(task_id, effect, not current)

    task = await db_instance.get_task(task_id) # Recargar
    keyboard = build_processing_menu(task_id, task['file_type'], task, task.get('original_filename', ''))
    await query.message.edit_text(
        f"🛠️ Configuración actualizada.",
        reply_markup=keyboard, parse_mode=ParseMode.HTML
    )

@Client.on_callback_query(filters.regex(r"^song_select_"))
async def on_song_select(client: Client, query: CallbackQuery):
    result_id = query.data.split("_")[2]
    user = query.from_user

    await query.message.edit_text(f"✅ Canción seleccionada. Preparando...", parse_mode=ParseMode.HTML)

    search_result = await db_instance.search_results.find_one({"_id": ObjectId(result_id), "user_id": user.id})
    if not search_result: return await query.message.edit_text("❌ Error: Resultado expirado o no es tuyo.")
    
    if search_id := search_result.get('search_id'):
        await db_instance.search_results.delete_many({"search_id": search_id})
        await db_instance.search_sessions.delete_one({"_id": ObjectId(search_id)})

    search_term_or_url = search_result.get('url') or f"ytsearch1:{search_result.get('search_term')}"
    
    info = await asyncio.to_thread(downloader.get_url_info, search_term_or_url)
    if not info or not info.get('formats'):
        return await query.message.edit_text("❌ No pude obtener información para esa selección.")
    
    if info.get('is_video'):
        task_id = await db_instance.add_task(user_id=user.id, file_type='video', url=info['url'], file_name=sanitize_filename(info['title']), url_info=info)
        keyboard = build_download_quality_menu(str(task_id), info['formats'])
        text = f"✅ Video seleccionado:\n\n<b>{escape_html(info['title'])}</b>\n\nSeleccione el formato a descargar:"
        await query.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    else:
        best_audio_format = downloader.get_best_audio_format(info['formats'])
        lyrics = await asyncio.to_thread(downloader.get_lyrics, info['url'])

        processing_config = {
            "download_format_id": best_audio_format,
            "lyrics": lyrics,
            "thumbnail_url": info.get('thumbnail')
        }
        
        task_id = await db_instance.add_task(
            user_id=user.id, file_type='audio', url=info['url'], 
            file_name=sanitize_filename(info['title']), 
            url_info=info, processing_config=processing_config
        )

        if not task_id: return await query.message.edit_text("❌ Error al crear la tarea en la DB.")
        
        await db_instance.update_task(str(task_id), "status", "queued")
        
        await query.message.edit_text(f"🔥 <b>{escape_html(info['title'])}</b>\n\nTu canción ha sido enviada a la forja. El procesamiento comenzará en breve.", parse_mode=ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^search_page_"))
async def on_search_page(client: Client, query: CallbackQuery):
    await query.answer()
    parts = query.data.split("_")
    search_id, page = parts[2], int(parts[3])

    session = await db_instance.search_sessions.find_one({"_id": ObjectId(search_id)})
    if not session: return await query.message.edit_text("❌ Esta sesión de búsqueda ha expirado.")

    all_results = await db_instance.search_results.find({"search_id": search_id}).to_list(length=100)
    if not all_results: return await query.message.edit_text("❌ No se encontraron resultados para esta sesión.")

    keyboard = build_search_results_keyboard(all_results, search_id, page)
    await query.message.edit_text(
        f"✅ Resultados para: <b>{escape_html(session['query'])}</b>",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@Client.on_callback_query(filters.regex(r"^cancel_search_"))
async def on_cancel_search(client: Client, query: CallbackQuery):
    await query.answer()
    search_id = query.data.split("_")[2]
    await db_instance.search_results.delete_many({"search_id": search_id})
    await db_instance.search_sessions.delete_one({"_id": ObjectId(search_id)})
    await query.message.edit_text("✅ Búsqueda cancelada y resultados limpiados.")