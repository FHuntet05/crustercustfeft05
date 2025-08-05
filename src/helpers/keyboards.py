from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .utils import escape_html

# =================================================================
# 1. MENÚ DEL PANEL PRINCIPAL
# =================================================================
def build_panel_keyboard(tasks):
    keyboard = []
    for task in tasks:
        task_id = str(task.get('_id'))
        # Si la tarea viene de una URL, el nombre puede no existir al principio
        display_name = task.get('original_filename') or task.get('url', 'Tarea de URL')
        short_name = (display_name[:30] + '...') if len(display_name) > 33 else display_name
        keyboard.append([InlineKeyboardButton(f"🎬 {escape_html(short_name)}", callback_data=f"task_process_{task_id}")])
    
    if tasks:
        keyboard.append([
            InlineKeyboardButton("✨ Procesar Todo (Bulk)", callback_data="bulk_start"),
            InlineKeyboardButton("💥 Limpiar Panel", callback_data="panel_delete_all")
        ])
    return InlineKeyboardMarkup(keyboard)

# =================================================================
# 2. MENÚ DE PROCESAMIENTO PRINCIPAL (POR TIPO DE ARCHIVO)
# =================================================================
def build_processing_menu(task_id, file_type, task_config):
    keyboard = []
    # --- MENÚ DE VIDEO ---
    if file_type == 'video':
        quality_text = f"⚙️ Calidad ({task_config.get('quality', '720p')})"
        watermark_text = f"💧 Marca de Agua ({'ON' if task_config.get('watermark_enabled') else 'OFF'})"
        keyboard.extend([
            [InlineKeyboardButton(quality_text, callback_data=f"config_quality_{task_id}")],
            [InlineKeyboardButton(watermark_text, callback_data=f"config_watermark_{task_id}")],
            [InlineKeyboardButton("✂️ Cortar", callback_data=f"config_trim_{task_id}"), InlineKeyboardButton("🧩 Dividir", callback_data=f"config_split_{task_id}")],
            [InlineKeyboardButton("📸 Capturas", callback_data=f"config_screenshot_{task_id}"), InlineKeyboardButton("🎞️ a GIF", callback_data=f"config_gif_{task_id}")],
            [InlineKeyboardButton("🔇 Silenciar", callback_data=f"config_mute_{task_id}")],
            [InlineKeyboardButton("🎵/📜 Pistas", callback_data=f"config_tracks_{task_id}")],
        ])
    # --- MENÚ DE AUDIO ---
    elif file_type == 'audio':
        bitrate = task_config.get('audio_bitrate', '128k')
        keyboard.extend([
            [InlineKeyboardButton(f"🔊 Convertir ({bitrate})", callback_data=f"config_audio_convert_{task_id}")],
            [InlineKeyboardButton("✂️ Cortar", callback_data=f"config_audio_trim_{task_id}")],
            [InlineKeyboardButton("🎧 EQ/Bass/Velocidad", callback_data=f"config_audio_eq_{task_id}")],
            [InlineKeyboardButton("🖼️ Editar Tags", callback_data=f"config_audio_tags_{task_id}")],
        ])
    # --- MENÚ DE DOCUMENTO ---
    elif file_type == 'document':
        keyboard.extend([
            [InlineKeyboardButton("📦 Extraer Archivo", callback_data=f"config_extract_{task_id}")],
            [InlineKeyboardButton("📜 Convertir Subtítulo", callback_data=f"config_sub_convert_{task_id}")],
        ])

    # --- BOTONES COMUNES ---
    keyboard.extend([
        [InlineKeyboardButton("✏️ Renombrar", callback_data=f"config_rename_{task_id}")],
        [InlineKeyboardButton("📦 Comprimir en ZIP", callback_data=f"config_zip_{task_id}")],
        [InlineKeyboardButton("📊 Info del Media", callback_data=f"config_info_{task_id}")],
        [
            InlineKeyboardButton("🔙 Volver al Panel", callback_data="panel_show"),
            InlineKeyboardButton("✅ Enviar a la Cola", callback_data=f"task_queue_{task_id}")
        ]
    ])
    return InlineKeyboardMarkup(keyboard)

# =================================================================
# 3. SUB-MENÚS DE CONFIGURACIÓN
# =================================================================
def build_quality_menu(task_id):
    keyboard = [
        [InlineKeyboardButton("1080p", callback_data=f"set_quality_{task_id}_1080p")],
        [InlineKeyboardButton("720p", callback_data=f"set_quality_{task_id}_720p")],
        [InlineKeyboardButton("480p", callback_data=f"set_quality_{task_id}_480p")],
        [InlineKeyboardButton("360p", callback_data=f"set_quality_{task_id}_360p")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"task_process_{task_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_watermark_menu(task_id, is_enabled):
    toggle_text = "🔴 Desactivar" if is_enabled else "🟢 Activar"
    keyboard = [
        [InlineKeyboardButton(toggle_text, callback_data=f"set_watermark_{task_id}_toggle")],
        [InlineKeyboardButton("Posición", callback_data=f"set_watermark_{task_id}_position")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"task_process_{task_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

# =================================================================
# 4. BOTONES GENÉRICOS
# =================================================================
def build_back_button(callback_data):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data=callback_data)]])