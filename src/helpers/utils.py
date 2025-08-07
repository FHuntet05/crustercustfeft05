# src/helpers/utils.py

import os
from html import escape
from datetime import timedelta

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
        percentage = max(0, min(100, percentage)) # Clamp percentage between 0 and 100
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
    
    invalid_chars = r'<>:"/\|?*' + '\x00-\x1f\x7f' # Añadir caracteres de control
    sanitized = "".join(c if c not in invalid_chars else '_' for c in filename)
    # Reemplazar múltiples espacios con uno solo
    sanitized = " ".join(sanitized.split())
    # Limitar la longitud total del nombre de archivo
    return sanitized[:200]

def format_status_message(
    operation: str, filename: str, percentage: float, 
    processed_bytes: float, total_bytes: float, speed: float, 
    eta: float, engine: str, user_id: int, user_mention: str
) -> str:
    """Construye el mensaje de estado con el formato visual solicitado."""
    bar = _create_text_bar(percentage, 10)
    short_filename = (filename[:45] + '...') if len(filename) > 48 else filename

    status_line = operation # La operación ya viene con emoji
    speed_text = f"{format_bytes(speed)}/s" if speed > 1 else f"{speed:.2f}x" if "Codificando" in operation else f"{format_bytes(speed)}/s"
    processed_text = f"{format_bytes(processed_bytes)}" if "Codificando" not in operation else f"{format_time(processed_bytes)}"
    total_text = f"{format_bytes(total_bytes)}" if "Codificando" not in operation else f"{format_time(total_bytes)}"

    lines = [
        f"┏ ꜰɪʟᴇɴᴀᴍᴇ: <code>{escape_html(short_filename)}</code>",
        f"┠ [{bar}] {percentage:.2f}%",
        f"┠ ᴘʀᴏᴄᴇssᴇᴅ: {processed_text} / {total_text}",
        f"┠ sᴛᴀᴛᴜs: {status_line}",
        f"┠ ᴇɴɢɪɴᴇ: {engine}",
        f"┠ sᴘᴇᴇᴅ: {speed_text}",
        f"┠ ᴇᴛᴀ: {format_time(eta)}",
        f"┗ ᴜsᴇʀ: {user_mention} | ɪᴅ: <code>{user_id}</code>"
    ]
    greeting = get_greeting(user_id).replace(', ', '')
    return f"<b>{greeting} {operation}</b>\n\n" + "\n".join(lines)