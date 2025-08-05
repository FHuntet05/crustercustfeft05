import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import build_back_button, build_processing_menu
from src.helpers.utils import get_greeting, escape_html

logger = logging.getLogger(__name__)

async def show_config_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: str, menu_type: str, payload: str = None):
    """
    Función genérica para mostrar un menú de configuración y pedir entrada al usuario.
    Guarda el estado de la configuración en context.user_data.
    """
    query = update.callback_query
    greeting_prefix = get_greeting(query.from_user.id)
    
    # Para bulk rename, task_id es en realidad una lista de IDs
    if menu_type != 'bulkrename':
        task = db_instance.get_task(task_id)
        if not task:
            await query.edit_message_text("❌ Error: Tarea no encontrada.", reply_markup=None)
            return
        original_filename = task.get('original_filename', 'archivo')
    else:
        original_filename = f"{len(task_id.split(','))} tareas"

    text = ""
    # Guardar en el contexto qué estamos configurando y para qué tarea(s)
    # La estructura de active_config varía según la necesidad del menú
    
    # Flujos que inician una conversación de varios pasos
    if menu_type == "audiotags":
        context.user_data['active_config'] = {"task_id": task_id, "menu_type": "audiotags", "stage": "title"}
        text = f"🖼️ <b>Editar Tags</b>\n\n{greeting_prefix}envíeme el nuevo <b>título</b> de la canción.\nO envíe /skip para omitir."
    
    # Flujos que esperan un solo input de texto
    else:
        # Para bulk rename, el task_id es la lista de IDs
        context.user_data['active_config'] = {"task_id": task_id, "menu_type": menu_type, "payload": payload}
        menu_texts = {
            "rename": f"✏️ <b>Renombrar Archivo</b>\n\n{greeting_prefix}envíeme el nuevo nombre para <code>{escape_html(original_filename)}</code>.\n<i>No incluya la extensión del archivo.</i>",
            "trim": f"✂️ <b>Cortar Video</b>\n\n{greeting_prefix}envíeme el tiempo de inicio y fin.\nFormatos: <code>HH:MM:SS-HH:MM:SS</code> o <code>MM:SS-MM:SS</code>.",
            "audiotrim": f"✂️ <b>Cortar Audio</b>\n\n{greeting_prefix}envíeme el tiempo de inicio y fin.\nFormatos: <code>HH:MM:SS-HH:MM:SS</code> o <code>MM:SS-MM:SS</code>.",
            "split": f"🧩 <b>Dividir Video</b>\n\n{greeting_prefix}envíeme el criterio de división: por tiempo (ej. <code>300s</code>) o por tamaño (ej. <code>50MB</code>).",
            "gif": f"🎞️ <b>Crear GIF</b>\n\n{greeting_prefix}envíeme la duración en segundos y los FPS.\nFormato: <code>[duración] [fps]</code> (ej: <code>5 15</code> para 5s a 15fps).",
            "screenshot": f"📸 <b>Capturas de Pantalla</b>\n\n{greeting_prefix}envíeme los timestamps de las capturas, separados por comas.\n(ej: <code>00:10, 01:25, 50%</code>).",
            "caption": f"📄 <b>Editar Caption y Botones</b>\n\nPrimero, envíeme el nuevo texto para el caption. Luego le pediré los botones.",
            "addtrack": f"➕ <b>Añadir Pista</b>\n\n{greeting_prefix}envíeme ahora el archivo de <b>{payload}</b> que desea añadir al video.",
            "audioeffect": f"🎧 <b>Ajustar Efecto</b>\n\n{greeting_prefix}envíeme el valor para <b>{payload}</b>.",
            "bulkrename": f"✏️ <b>Renombrar en Lote</b>\n\n{greeting_prefix}envíeme el patrón de nombre. Use <code>{{num}}</code> para el número de secuencia (ej: <code>Serie S01E{{num}}</code>)."
        }
        text = menu_texts.get(menu_type, "Configuración no reconocida.")
        
    back_button_cb = f"task_process_{task_id}" if menu_type != 'bulkrename' else "panel_show"
    keyboard = build_back_button(back_button_cb)
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manejador genérico de texto que procesa la entrada del usuario según el menú de configuración
    que esté activo en context.user_data.
    """
    if 'active_config' not in context.user_data:
        # Si no hay configuración activa, no hacer nada.
        return
        
    config = context.user_data['active_config']
    user_input = update.message.text.strip()
    
    # --- Manejar cancelación o skip ---
    if user_input.lower() == "/skip":
        await update.message.reply_html("Acción omitida.")
        # Lógica para avanzar al siguiente paso si es una conversación
        if config['menu_type'] == 'audiotags':
            config['stage'] = 'artist' # Avanzar de todas formas
            await _handle_audio_tags_conversation(update, context, config, None)
        else:
            context.user_data.pop('active_config', None)
        return

    # --- Delegar al manejador de conversación correspondiente ---
    if config['menu_type'] == 'audiotags':
        await _handle_audio_tags_conversation(update, context, config, user_input)
    elif config['menu_type'] == 'bulkrename':
        await _handle_bulk_rename(update, context, config, user_input)
    else:
        # Para todos los demás flujos de un solo paso
        await _handle_single_input(update, context, config, user_input)


async def _handle_single_input(update: Update, context: ContextTypes.DEFAULT_TYPE, config: dict, user_input: str):
    """Procesa configuraciones que solo requieren una entrada de texto."""
    task_id = config['task_id']
    menu_type = config['menu_type']
    feedback_message = ""
    
    # Mapeo de menu_type a (clave_db, mensaje_feedback)
    single_input_map = {
        "rename": ("final_filename", f"✅ Nombre de archivo de salida actualizado a <code>{escape_html(user_input)}</code>."),
        "trim": ("trim_times", f"✅ Tiempos de corte establecidos: <code>{escape_html(user_input)}</code>."),
        "audiotrim": ("trim_times", f"✅ Tiempos de corte establecidos: <code>{escape_html(user_input)}</code>."),
        "split": ("split_criteria", f"✅ Criterio de división establecido: <code>{escape_html(user_input)}</code>."),
        "screenshot": ("screenshot_points", f"✅ Puntos de captura establecidos."),
        "caption": ("final_caption", "✅ Caption actualizado."),
    }

    if menu_type in single_input_map:
        db_key, feedback_message = single_input_map[menu_type]
        db_instance.update_task_config(task_id, db_key, user_input)
    
    elif menu_type == "gif":
        try:
            duration, fps = user_input.split()
            db_instance.update_task_config(task_id, "gif_options", {"duration": duration, "fps": fps})
            feedback_message = f"✅ GIF se creará con {duration}s de duración a {fps}fps."
        except ValueError:
            feedback_message = "❌ Formato incorrecto. Debe ser: <code>[duración] [fps]</code>."
    
    # Limpiar estado y devolver al menú
    context.user_data.pop('active_config', None)
    await update.message.reply_html(feedback_message)
    
    task = db_instance.get_task(task_id)
    if task:
        keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
        await update.message.reply_html("¿Algo más?", reply_markup=keyboard)


async def _handle_audio_tags_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE, config: dict, user_input: str or None):
    """Maneja la conversación de varios pasos para editar tags de audio."""
    task_id = config['task_id']
    stage = config.get('stage')
    
    next_stage, prompt, db_key = None, None, None

    if stage == "title":
        if user_input: db_instance.update_task_config(task_id, "audio_tags.title", user_input)
        next_stage, prompt = "artist", "Ahora envíeme el <b>artista</b>. (/skip)"
    elif stage == "artist":
        if user_input: db_instance.update_task_config(task_id, "audio_tags.artist", user_input)
        next_stage, prompt = "album", "Ahora envíeme el <b>álbum</b>. (/skip)"
    elif stage == "album":
        if user_input: db_instance.update_task_config(task_id, "audio_tags.album", user_input)
        next_stage, prompt = "cover", "Finalmente, envíeme la <b>imagen de la carátula</b>. (/skip)"
    
    if next_stage:
        context.user_data['active_config']['stage'] = next_stage
        await update.message.reply_html(prompt)
    else: # Conversación terminada
        context.user_data.pop('active_config', None)
        await update.message.reply_html("✅ Configuración de tags guardada.")
        task = db_instance.get_task(task_id)
        if task:
            keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
            await update.message.reply_html("¿Algo más?", reply_markup=keyboard)

async def _handle_bulk_rename(update: Update, context: ContextTypes.DEFAULT_TYPE, config: dict, user_input: str):
    """Maneja la lógica de renombrado en lote."""
    task_ids = config['task_id'].split(',')
    pattern = user_input
    renamed_count = 0
    
    tasks_to_rename = db_instance.get_multiple_tasks(task_ids)

    for i, task in enumerate(tasks_to_rename):
        try:
            # Asegurar que {num} exista en el patrón
            final_name = pattern.format(num=str(i + 1).zfill(2))
        except (KeyError, IndexError):
            await update.message.reply_html("❌ Patrón inválido. Asegúrese de usar <code>{num}</code> correctamente.")
            context.user_data.pop('active_config', None)
            return
            
        db_instance.update_task(str(task['_id']), "final_filename", final_name)
        renamed_count += 1
    
    context.user_data.pop('active_config', None)
    await update.message.reply_html(f"✅ {renamed_count} tareas renombradas. Vuelva al /panel para ver los cambios.")


async def photo_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador específico para recibir la carátula del audio durante la configuración."""
    config = context.user_data.get('active_config', {})
    if not (config.get('menu_type') == 'audiotags' and config.get('stage') == 'cover'):
        return

    context.user_data.pop('active_config')
    task_id = config['task_id']
    photo = update.message.photo[-1] # Coger la de mayor resolución

    db_instance.update_task_config(task_id, "audio_tags.cover_file_id", photo.file_id)
    
    await update.message.reply_html("✅ Carátula recibida y guardada.")
    task = db_instance.get_task(task_id)
    if task:
        keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
        await update.message.reply_html("Toda la información de los tags ha sido guardada. ¿Algo más?", reply_markup=keyboard)