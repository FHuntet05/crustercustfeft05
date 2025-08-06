import os
from html import escape
from datetime import timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Cargar el ID del admin desde las variables de entorno de forma segura
try:
    ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
except (TypeError, ValueError):
    ADMIN_USER_ID = None 

def get_greeting(user_id: int) -> str:
    """Devuelve un saludo personalizado si el usuario es el administrador."""
    return "Jefe, " if user_id == ADMIN_USER_ID else ""

def format_bytes(size_in_bytes) -> str:
    """Formatea un tamaño en bytes a un formato legible (KB, MB, GB)."""
    if size_in_bytes is None: return "N/A"
    try:
        size = float(size_in_bytes)
        if size < 0: return "N/A"
        if size == 0: return "0 B"
        power = 1024
        n = 0
        power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
        while size >= power and n < len(power_labels) - 1:
            size /= power
            n += 1
        return f"{size:.2f} {power_labels[n]}"
    except (ValueError, TypeError):
        return "Tamaño inválido"

def escape_html(text: str) -> str:
    """Escapa caracteres HTML de un texto para evitar problemas de parseo en Telegram."""
    if not isinstance(text, str): 
        return ""
    return escape(text, quote=False)

def _create_text_bar(percentage: float, length: int = 10, fill_char: str = '█', empty_char: str = '░') -> str:
    """Crea una barra de progreso de texto simple."""
    if not 0 <= percentage <= 100: 
        percentage = 0
    filled_len = int(length * percentage / 100)
    bar = fill_char * filled_len + empty_char * (length - filled_len)
    return bar

def format_time(seconds: float) -> str:
    """Formatea segundos a un formato HH:MM:SS."""
    if seconds is None or not isinstance(seconds, (int, float)) or seconds < 0 or seconds == float('inf'):
        return "∞"
    return str(timedelta(seconds=int(seconds)))

def sanitize_filename(filename: str) -> str:
    """Elimina caracteres inválidos de un nombre de archivo para compatibilidad con sistemas de archivos."""
    if not isinstance(filename, str):
        return "archivo_invalido"
    
    invalid_chars = r'<>:"/\|?*'
    sanitized = "".join(c if c not in invalid_chars else '_' for c in filename)
    sanitized = " ".join(sanitized.split())
    return sanitized[:200]

def parse_reply_markup(text: str) -> dict or None:
    """
    Parsea un texto con formato 'texto1 - url1, texto2 - url2'
    y lo convierte en un diccionario serializable para un InlineKeyboardMarkup.
    Devuelve None si el formato es inválido.
    """
    if not text or not isinstance(text, str):
        return None
    
    keyboard = []
    button_pairs = text.split(',')
    
    for pair in button_pairs:
        parts = pair.split('-', 1)
        if len(parts) == 2:
            text = parts[0].strip()
            url = parts[1].strip()
            if text and url:
                keyboard.append([{"text": text, "url": url}])
        else:
            return None
            
    return {"inline_keyboard": keyboard} if keyboard else None

def format_status_message(operation: str, filename: str, percentage: float, processed_bytes: float, total_bytes: float, speed: float, eta: float, engine: str, user_id: int, user_mention: str) -> str:
    """Construye el mensaje de estado con el formato visual solicitado."""
    bar = _create_text_bar(percentage, 10)
    short_filename = (filename[:45] + '...') if len(filename) > 48 else filename

    status_line = "📥 Descargando" if "descarg" in operation.lower() else "⬆️ Subiendo" if "subiendo" in operation.lower() else "⚙️ Codificando"

    lines = [
        f"┏ ꜰɪʟᴇɴᴀᴍᴇ: <code>{escape_html(short_filename)}</code>",
        f"┠ [{bar}] {percentage:.2f}%",
        f"┠ ᴘʀᴏᴄᴇssᴇᴅ: {format_bytes(processed_bytes)} / {format_bytes(total_bytes)}",
        f"┠ sᴛᴀᴛᴜs: {status_line}",
        f"┠ ᴇɴɢɪɴᴇ: {engine}",
        f"┠ sᴘᴇᴇᴅ: {format_bytes(speed)}/s",
        f"┠ ᴇᴛᴀ: {format_time(eta)}",
        f"┗ ᴜsᴇʀ: {user_mention} | ɪᴅ: <code>{user_id}</code>"
    ]
    return f"<b>{operation}</b>\n\n" + "\n".join(lines)