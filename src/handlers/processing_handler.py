import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import build_back_button, build_processing_menu
from src.helpers.utils import get_greeting, escape_html, parse_reply_markup

logger = logging.getLogger(__name__)

async def show_config_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: str, menu_type: str, payload: str = None):
    """
    Función genérica para mostrar un menú de configuración y pedir entrada al usuario.
    """
    query = update.callback_query
    greeting_prefix = get_greeting(query.from_user.id)
    
    if menu_type != 'bulkrename':
        task = db_instance.get_task(task_id)
        if not task:
            await query.edit_message_text("❌ Error: Tarea no encontrada.", reply_markup=None)
            return
        original_filename = task.get('original_filename', 'archivo')
    else:
        original_filename = f"{len(task_id.split(','))} tareas"

    text = ""
    context.user_data['active_config'] = {"task_id": task_id, "menu_type": menu_type, "payload": payload}
    
    if menu_type == "audiotags":
        context.user_data['active_config']["stage"] = "title"
        text = f"🖼️ <b>Editar Tags</b>\n\n{greeting_prefix}envíeme el nuevo <b>título</b> de la canción.\nO envíe /skip para omitir."
    elif menu_type == "caption":
        context.user_data['active_config']["stage"] = "text"
        text = f"📄 <b>Editar Caption y Botones</b>\n\n{greeting_prefix}primero, envíeme el nuevo texto para el <b>caption</b>.\nEnvíe /skip para no cambiar el caption."
    else:
        menu_texts = {
            "rename": f"✏️ <b>Renombrar Archivo</b>\n\n{greeting_prefix}envíeme el nuevo nombre para <code>{escape_html(original_filename)}</code>.\n<i>No incluya la extensión.</i>",
            "trim": f"✂️ <b>Cortar</b>\n\n{greeting_prefix}envíeme el tiempo de inicio y fin.\nFormatos: <code>HH:MM:SS-HH:MM:SS</code> o <code>MM:SS-MM:SS</code>.",
            "split": f"🧩 <b>Dividir Video</b>\n\n{greeting_prefix}envíeme el criterio de división por tiempo (ej. <code>300s</code>).",
            "gif": f"🎞️ <b>Crear GIF</b>\n\n{greeting_prefix}envíeme la duración y los FPS.\nFormato: <code>[duración] [fps]</code> (ej: <code>5 15</code>).",
            "screenshot": f"📸 <b>Capturas</b>\n\n{greeting_prefix}envíeme los timestamps, separados por comas.\n(ej: <code>00:10, 01:25, 50%</code>).",
            "sample": f"🎞️ <b>Crear Muestra</b>\n\n{greeting_prefix}envíeme la duración en segundos (ej: <code>30</code>).",
            "extract": f"📦 <b>Extraer Archivo</b>\n\n{greeting_prefix}envíeme la contraseña. Envíe /skip si no tiene.",
            "addtrack": f"➕ <b>Añadir Pista</b>\n\n{greeting_prefix}envíeme ahora el archivo de <b>{payload}</b> que desea añadir.",
            "bulkrename": f"✏️ <b>Renombrar en Lote</b>\n\n{greeting_prefix}envíeme el patrón. Use <code>{{num}}</code> para la secuencia (ej: <code>S01E{{num}}</code>)."
        }
        text = menu_texts.get(menu_type, "Configuración no reconocida.")
        
    back_button_cb = f"task_process_{task_id}" if menu_type != 'bulkrename' else "panel_show"
    keyboard = build_back_button(back_button_cb)
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE, config: dict, user_input: str or None):
    """Procesa la entrada de texto del usuario según el menú de configuración activo."""
    menu_type = config.get('menu_type')
    if menu_type == 'audiotags':
        await _handle_audio_tags_conversation(update, context, config, user_input)
    elif menu_type == 'caption':
        await _handle_caption_conversation(update, context, config, user_input)
    elif menu_type == 'bulkrename':
        await _handle_bulk_rename(update, context, config, user_input)
    else:
        await _handle_single_input(update, context, config, user_input)

async def handle_cover_art_input(update: Update, context: ContextTypes.DEFAULT_TYPE, config: dict):
    """Manejador para recibir la carátula del audio."""
    context.user_data.pop('active_config', None)
    task_id = config['task_id']
    photo = update.message.photo[-1]
    db_instance.update_task_config(task_id, "audio_tags.cover_file_id", photo.file_id)
    await update.message.reply_html("✅ Carátula recibida y guardada.")
    task = db_instance.get_task(task_id)
    if task:
        keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
        await update.message.reply_html("Toda la información de los tags ha sido guardada. ¿Algo más?", reply_markup=keyboard)

async def handle_track_input(update: Update, context: ContextTypes.DEFAULT_TYPE, config: dict):
    """Manejador para recibir archivos de pistas (audio/subtítulos)."""
    context.user_data.pop('active_config', None)
    task_id, track_type = config['task_id'], config['payload']
    file_obj = update.message.document or update.message.audio
    if not file_obj: return
    db_instance.update_task_config(task_id, f"add_{track_type}_file_id", file_obj.file_id)
    await update.message.reply_html(f"✅ Pista de {track_type} recibida.")
    task = db_instance.get_task(task_id)
    if task:
        keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
        await update.message.reply_html("Puede continuar configurando o enviar a la cola.", reply_markup=keyboard)


# --- Lógica Interna de Conversaciones ---

async def _handle_single_input(update: Update, context: ContextTypes.DEFAULT_TYPE, config: dict, user_input: str or None):
    task_id, menu_type = config['task_id'], config['menu_type']
    if user_input is None:
        feedback_message = "Acción cancelada."
    else:
        db_key, feedback_message = None, "✅ Configuración guardada."
        actions = {
            "rename": ("final_filename", f"✅ Nombre actualizado a <code>{escape_html(user_input)}</code>."),
            "trim": ("trim_times", f"✅ Tiempos de corte: <code>{escape_html(user_input)}</code>."),
            "split": ("split_criteria", f"✅ Criterio de división: <code>{escape_html(user_input)}</code>."),
            "screenshot": ("screenshot_points", "✅ Puntos de captura establecidos."),
            "sample": ("sample_duration", f"✅ Muestra se creará con {user_input}s."),
            "extract": ("archive_password", "✅ Contraseña guardada.")
        }
        if menu_type in actions:
            db_key, feedback_message = actions[menu_type]
            db_instance.update_task_config(task_id, db_key, user_input)
        elif menu_type == "gif":
            try:
                duration, fps = user_input.split()
                db_instance.update_task_config(task_id, "gif_options", {"duration": duration, "fps": fps})
                feedback_message = f"✅ GIF se creará con {duration}s a {fps}fps."
            except ValueError:
                feedback_message = "❌ Formato incorrecto: <code>[duración] [fps]</code>."
    
    context.user_data.pop('active_config', None)
    await update.message.reply_html(feedback_message)
    task = db_instance.get_task(task_id)
    if task:
        keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
        await update.message.reply_html("¿Algo más?", reply_markup=keyboard)

async def _handle_audio_tags_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE, config: dict, user_input: str or None):
    task_id, stage = config['task_id'], config.get('stage')
    next_stage, prompt = None, None
    stages = {
        "title": ("audio_tags.title", "artist", "Ahora envíeme el <b>artista</b>. (/skip)"),
        "artist": ("audio_tags.artist", "album", "Ahora envíeme el <b>álbum</b>. (/skip)"),
        "album": ("audio_tags.album", "cover", "Finalmente, envíeme la <b>imagen de la carátula</b>. (/skip)")
    }
    if stage in stages:
        db_key, next_stage, prompt = stages[stage]
        if user_input: db_instance.update_task_config(task_id, db_key, user_input)
    
    if next_stage:
        context.user_data['active_config']['stage'] = next_stage
        await update.message.reply_html(prompt)
    else:
        context.user_data.pop('active_config', None)
        await update.message.reply_html("✅ Configuración de tags guardada.")
        task = db_instance.get_task(task_id)
        if task:
            keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
            await update.message.reply_html("¿Algo más?", reply_markup=keyboard)

async def _handle_caption_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE, config: dict, user_input: str or None):
    task_id, stage = config['task_id'], config.get('stage')
    if stage == "text":
        if user_input: db_instance.update_task_config(task_id, "final_caption", user_input)
        context.user_data['active_config']['stage'] = 'buttons'
        await update.message.reply_html("✅ Caption guardado.\n\nAhora, envíeme los botones.\nFormato: <code>texto1 - url1, texto2 - url2</code>\nEnvíe /skip para no añadir botones.")
    elif stage == "buttons":
        context.user_data.pop('active_config', None)
        if user_input:
            if reply_markup := parse_reply_markup(user_input):
                db_instance.update_task_config(task_id, "reply_markup", reply_markup)
                await update.message.reply_html("✅ Botones guardados.")
            else:
                await update.message.reply_html("❌ Formato inválido. No se guardaron botones.")
        else:
             await update.message.reply_html("✅ No se añadieron botones.")
        task = db_instance.get_task(task_id)
        if task:
            keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
            await update.message.reply_html("Configuración finalizada. ¿Algo más?", reply_markup=keyboard)

async def _handle_bulk_rename(update: Update, context: ContextTypes.DEFAULT_TYPE, config: dict, user_input: str):
    task_ids, pattern = config['task_id'].split(','), user_input
    if '{num}' not in pattern:
        await update.message.reply_html("❌ Patrón inválido. Debe incluir <code>{num}</code>.")
        context.user_data.pop('active_config', None)
        return
    
    tasks = db_instance.get_multiple_tasks(task_ids)
    for i, task in enumerate(tasks):
        final_name = pattern.replace('{num}', str(i + 1).zfill(len(str(len(tasks)))))
        db_instance.update_task_config(str(task['_id']), "final_filename", final_name)
    
    context.user_data.pop('active_config', None)
    await update.message.reply_html(f"✅ {len(tasks)} tareas renombradas.")