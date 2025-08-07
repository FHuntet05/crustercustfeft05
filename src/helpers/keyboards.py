# src/helpers/keyboards.py

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from . import utils  # Aseguramos la importación de utils

def create_search_results_keyboard(results, current_page, total_pages, query_id):
    """
    Crea un teclado inline con los resultados de búsqueda de música, paginado.
    """
    keyboard = []
    
    # Botones para cada resultado de la página actual
    for result in results:
        button_text = f"🎵 {result['title']} - {result['artist']} ({result['duration']})"
        # --- CAMBIO CLAVE ---
        # El callback_data ahora es específico y contiene el ID del video.
        callback_data = f"search_select_{result['id']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

    # Fila de paginación
    row = []
    if current_page > 1:
        row.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"sp_{current_page-1}_{query_id}"))
    
    row.append(InlineKeyboardButton(f"Pág {current_page}/{total_pages}", callback_data="noop")) # No-operation button
    
    if current_page < total_pages:
        row.append(InlineKeyboardButton("Siguiente ➡️", callback_data=f"sp_{current_page+1}_{query_id}"))
    
    if row:
        keyboard.append(row)

    return InlineKeyboardMarkup(keyboard)

def create_format_selection_keyboard(formats, task_id):
    """
    Crea un teclado para que el usuario seleccione el formato de video o audio.
    """
    keyboard = []
    video_formats = []
    audio_formats = []

    # Ordenar formatos por calidad descendente
    sorted_formats = sorted(formats, key=lambda x: x.get('height', 0) or x.get('abr', 0), reverse=True)

    for f in sorted_formats:
        format_id = f['format_id']
        ext = f.get('ext')
        
        # Formatos de video (con video y audio)
        if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
            resolution = f.get('resolution', 'N/A')
            filesize = f.get('filesize') or f.get('filesize_approx')
            size_str = f" ({utils.format_bytes(filesize)})" if filesize else ""
            label = f"🎬 Video {resolution} [{ext}]{size_str}"
            video_formats.append(InlineKeyboardButton(label, callback_data=f"format_{format_id}_{task_id}"))
        # Formatos de solo audio
        elif f.get('vcodec') == 'none' and f.get('acodec') != 'none':
            abr = f.get('abr', 0)
            filesize = f.get('filesize') or f.get('filesize_approx')
            size_str = f" ({utils.format_bytes(filesize)})" if filesize else ""
            label = f"🎵 Audio {ext.upper()} ~{int(abr)}k{size_str}"
            audio_formats.append(InlineKeyboardButton(label, callback_data=f"format_{format_id}_{task_id}"))

    if video_formats:
        keyboard.append([InlineKeyboardButton("--- 📹 FORMATOS DE VIDEO 📹 ---", callback_data="noop")])
        for button in video_formats:
            keyboard.append([button])
            
    if audio_formats:
        keyboard.append([InlineKeyboardButton("--- 🎧 FORMATOS DE AUDIO 🎧 ---", callback_data="noop")])
        for button in audio_formats:
            keyboard.append([button])
            
    return InlineKeyboardMarkup(keyboard)

def create_processing_menu_keyboard(task_id):
    """
    Crea el menú de opciones de procesamiento para un archivo.
    """
    keyboard = [
        [InlineKeyboardButton("✂️ Cortar (Trim)", callback_data=f"process_trim_{task_id}")],
        [InlineKeyboardButton("📏 Dividir Video", callback_data=f"process_split_{task_id}")],
        [InlineKeyboardButton("✨ Crear GIF", callback_data=f"process_gif_{task_id}")],
        [InlineKeyboardButton("🎵 Convertir Audio", callback_data=f"process_convert_{task_id}")],
        [InlineKeyboardButton("🔊 Efectos de Audio", callback_data=f"process_effects_{task_id}")],
        [InlineKeyboardButton("🏷️ Editar Tags", callback_data=f"process_tags_{task_id}")],
        [InlineKeyboardButton("❌ Cancelar", callback_data=f"cancel_{task_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)