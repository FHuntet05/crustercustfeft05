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
    # Evita escapar las comillas, que son válidas en los textos de Telegram
    return escape(text, quote=False)

def create_progress_bar(percentage: float, length: int = 10) -> str:
    """Crea una barra de progreso de texto simple."""
    if not 0 <= percentage <= 100: 
        percentage = 0
    filled_len = int(length * percentage / 100)
    bar = '█' * filled_len + '░' * (length - filled_len)
    return f"[{bar}]"

def format_time(seconds: float) -> str:
    """Formatea segundos a un formato HH:MM:SS."""
    if seconds is None or not isinstance(seconds, (int, float)) or seconds < 0:
        return "N/A"
    return str(timedelta(seconds=int(seconds)))

def sanitize_filename(filename: str) -> str:
    """Elimina caracteres inválidos de un nombre de archivo para compatibilidad con sistemas de archivos."""
    if not isinstance(filename, str):
        return "archivo_invalido"
    
    # Lista de caracteres no permitidos en la mayoría de sistemas de archivos
    invalid_chars = r'<>:"/\|?*'
    
    # Reemplaza los caracteres inválidos por un guion bajo
    sanitized = "".join(c if c not in invalid_chars else '_' for c in filename)
    
    # Reemplaza espacios múltiples y saltos de línea por un solo espacio
    sanitized = " ".join(sanitized.split())
    
    # Limita la longitud total para evitar problemas de filesystem (255 es un límite común)
    return sanitized[:200]