import logging
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from pyrogram.enums import ParseMode

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import build_back_button, build_processing_menu
from src.helpers.utils import get_greeting, escape_html

logger = logging.getLogger(__name__)

# --- Manejador para los botones de configuración ---
@Client.on_callback_query(filters.regex(r"^config_"))
async def show_config_menu(client: Client, query: CallbackQuery):
    await query.answer()
    
    parts = query.data.split("_")
    menu_type, task_id = parts[1], "_".join(parts[2:])
    
    task = await db_instance.get_task(task_id)
    if not task:
        await query.message.edit_text("❌ Error: Tarea no encontrada.")
        return

    original_filename = task.get('original_filename', 'archivo')
    greeting_prefix = get_greeting(query.from_user.id)
    
    if not hasattr(client, 'user_data'):
        client.user_data = {}
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
    if not active_config:
        return

    task_id = active_config['task_id']
    menu_type = active_config['menu_type']
    
    del client.user_data[user_id]
    
    feedback_message = "✅ Configuración guardada."
    
    try:
        if menu_type == "rename":
            await db_instance.update_task_config(task_id, "final_filename", user_input)
            feedback_message = f"✅ Nombre actualizado a <code>{escape_html(user_input)}</code>."
        
        elif menu_type == "trim":
            await db_instance.update_task_config(task_id, "trim_times", user_input)
            feedback_message = f"✅ Tiempos de corte: <code>{escape_html(user_input)}</code>."
        
        elif menu_type == "split":
            await db_instance.update_task_config(task_id, "split_criteria", user_input)
            feedback_message = f"✅ Criterio de división: <code>{escape_html(user_input)}</code>."
            
        elif menu_type == "gif":
            try:
                duration, fps = user_input.split()
                await db_instance.update_task_config(task_id, "gif_options", {"duration": duration, "fps": fps})
                feedback_message = f"✅ GIF se creará con {duration}s a {fps}fps."
            except ValueError:
                feedback_message = "❌ Formato incorrecto. Debe ser <code>[duración] [fps]</code>."
    except Exception as e:
        logger.error(f"Error al procesar la entrada de configuración: {e}")
        await message.reply("❌ Ocurrió un error al guardar la configuración.")
        return

    # --- CORRECCIÓN CRÍTICA ---
    # Se cambia .reply_html() por .reply() con parse_mode=ParseMode.HTML
    await message.reply(feedback_message, parse_mode=ParseMode.HTML)
    
    task = await db_instance.get_task(task_id)
    if task:
        keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
        # --- CORRECCIÓN CRÍTICA ---
        await message.reply("¿Algo más?", reply_markup=keyboard, parse_mode=ParseMode.HTML)


# --- Manejador para los botones que establecen valores directamente ---
@Client.on_callback_query(filters.regex(r"^set_"))
async def set_value_callback(client: Client, query: CallbackQuery):
    await query.answer()

    parts = query.data.split("_")
    config_type, task_id = parts[1], parts[2]
    value = "_".join(parts[3:])

    task = await db_instance.get_task(task_id)
    if not task:
        await query.message.edit_text("❌ Error: Tarea no encontrada."); return

    if config_type == "quality":
        await db_instance.update_task_config(task_id, "quality", value)
    
    elif config_type == "mute" and value == "toggle":
        current_mute_status = task.get('processing_config', {}).get('mute_audio', False)
        await db_instance.update_task_config(task_id, "mute_audio", not current_mute_status)
    
    elif config_type == "audioprop":
        prop_key, prop_value = parts[3], parts[4]
        await db_instance.update_task_config(task_id, f"audio_{prop_key}", prop_value)

    elif config_type == "audioeffect":
        effect = parts[3]
        current_effect_status = task.get('processing_config', {}).get(effect, False)
        await db_instance.update_task_config(task_id, effect, not current_effect_status)

    task = await db_instance.get_task(task_id)
    keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
    await query.message.edit_text(
        f"🛠️ Configuración actualizada.\n\n¿Qué desea hacer con:\n<code>{escape_html(task.get('original_filename', '...'))}</code>?",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )