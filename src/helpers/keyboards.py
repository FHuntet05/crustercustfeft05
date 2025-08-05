from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .utils import format_bytes, escape_html

# --- Menú del Panel Principal ---
def build_panel_keyboard(tasks):
    """Construye el teclado para la mesa de trabajo (/panel)."""
    keyboard = []
    
    for task in tasks:
        task_id = str(task.get('_id'))
        file_name = escape_html(task.get('original_filename', 'Archivo sin nombre'))
        short_name = (file_name[:30] + '...') if len(file_name) > 33 else file_name
        
        keyboard.append([
            InlineKeyboardButton(f"🎬 {short_name}", callback_data=f"process_{task_id}")
        ])
    
    if tasks:
        keyboard.append([
            InlineKeyboardButton("✨ Procesar Todo", callback_data="process_all"),
            InlineKeyboardButton("💥 Limpiar Panel", callback_data="delete_all")
        ])
        
    return InlineKeyboardMarkup(keyboard)

# --- Menú de Procesamiento para un Archivo ---
def build_processing_menu(task_id, file_type):
    """Construye el menú principal de funciones para un tipo de archivo específico."""
    keyboard = []
    
    if file_type == 'video':
        keyboard.extend([
            [InlineKeyboardButton("⚙️ Optimizar/Convertir", callback_data=f"config_convert_{task_id}")],
            [InlineKeyboardButton("✂️ Cortar (Trimmer)", callback_data=f"config_trim_{task_id}")],
            [InlineKeyboardButton("💧 Añadir Marca de Agua", callback_data=f"config_watermark_{task_id}")],
            [InlineKeyboardButton("📜 Incrustar Subtítulos", callback_data=f"config_subs_{task_id}")],
            [InlineKeyboardButton("📸 Capturas", callback_data=f"config_screenshot_{task_id}")],
        ])
    elif file_type == 'audio':
        keyboard.extend([
            [InlineKeyboardButton("🔊 Convertir Formato/Calidad", callback_data=f"config_audio_convert_{task_id}")],
            [InlineKeyboardButton("✂️ Cortar (Trimmer)", callback_data=f"config_audio_trim_{task_id}")],
            [InlineKeyboardButton("🎧 Ecualizador (EQ)", callback_data=f"config_audio_eq_{task_id}")],
            [InlineKeyboardButton("🖼️ Editar Tags/Carátula", callback_data=f"config_audio_tags_{task_id}")],
        ])
    
    # Botones comunes a todos los tipos de archivo
    keyboard.extend([
        [InlineKeyboardButton("✏️ Renombrar", callback_data=f"config_rename_{task_id}")],
        [
            InlineKeyboardButton("🔙 Volver al Panel", callback_data="back_to_panel"),
            InlineKeyboardButton("✅ Enviar a la Cola", callback_data=f"queue_{task_id}")
        ]
    ])
    
    return InlineKeyboardMarkup(keyboard)

# --- Menú de confirmación genérico ---
def build_confirmation_menu(action_yes, action_no, text_yes="✅ Sí", text_no="❌ No"):
    """Construye un teclado simple de Sí/No."""
    keyboard = [[
        InlineKeyboardButton(text_yes, callback_data=action_yes),
        InlineKeyboardButton(text_no, callback_data=action_no)
    ]]
    return InlineKeyboardMarkup(keyboard)

# --- Botón de Volver ---
def build_back_button(callback_data):
    """Construye un solo botón para volver a un menú anterior."""
    keyboard = [[
        InlineKeyboardButton("🔙 Volver", callback_data=callback_data)
    ]]
    return InlineKeyboardMarkup(keyboard)