# src/helpers/keyboards.py

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from .utils import escape_html, format_bytes

# Nota: Los objetos de teclado se construyen directamente creando listas de listas de botones.

def build_panel_keyboard(tasks: list) -> InlineKeyboardMarkup:
    """Construye el teclado para el comando /panel con las tareas pendientes."""
    keyboard = []
    for task in tasks:
        task_id = str(task.get('_id'))
        file_type = task.get('file_type', 'document')
        emoji_map = {'video': '🎬', 'audio': '🎵', 'document': '📄'}
        emoji = emoji_map.get(file_type, '📁')
        display_name = task.get('original_filename') or task.get('url', 'Tarea de URL')
        short_name = (display_name[:35] + '...') if len(display_name) > 38 else display_name
        keyboard.append([InlineKeyboardButton(f"{emoji} {escape_html(short_name)}", callback_data=f"task_process_{task_id}")])
    
    if tasks:
        keyboard.append([InlineKeyboardButton("💥 Limpiar Panel", callback_data="panel_delete_all")])
        
    return InlineKeyboardMarkup(keyboard)

def build_processing_menu(task_id: str, file_type: str, task_config: dict, filename: str = "") -> InlineKeyboardMarkup:
    """Construye el menú principal de procesamiento para una tarea."""
    keyboard = []
    
    # Menú específico para tareas de URL que aún no han sido descargadas
    if task_config.get('url_info') and not task_config.get('download_format_id'):
         keyboard.append([InlineKeyboardButton("💿 Elegir Calidad de Descarga", callback_data=f"config_dlquality_{task_id}")])

    if file_type == 'video':
        quality_text = f"⚙️ Convertir ({task_config.get('quality', 'Original')})"
        mute_text = "🔇 Silenciar" if not task_config.get('mute_audio') else "🔊 Desilenciar"
        keyboard.extend([
            [InlineKeyboardButton(quality_text, callback_data=f"config_quality_{task_id}")],
            [InlineKeyboardButton("✂️ Cortar", callback_data=f"config_trim_{task_id}"), InlineKeyboardButton("🧩 Dividir", callback_data=f"config_split_{task_id}")],
            [InlineKeyboardButton("🎞️ a GIF", callback_data=f"config_gif_{task_id}"), InlineKeyboardButton("💧 Marca de Agua", callback_data=f"config_watermark_{task_id}")],
            [InlineKeyboardButton(mute_text, callback_data=f"set_mute_{task_id}_toggle")],
        ])
    elif file_type == 'audio':
        bitrate = task_config.get('audio_bitrate', '128k')
        audio_format = task_config.get('audio_format', 'mp3')
        keyboard.extend([
            [InlineKeyboardButton(f"🔊 Convertir ({audio_format.upper()}, {bitrate})", callback_data=f"config_audioconvert_{task_id}")],
            [InlineKeyboardButton("🎧 Efectos", callback_data=f"config_audioeffects_{task_id}")],
            [InlineKeyboardButton("✂️ Cortar", callback_data=f"config_trim_{task_id}")],
            [InlineKeyboardButton("🖼️ Editar Tags", callback_data=f"config_audiotags_{task_id}")],
        ])

    keyboard.extend([
        [InlineKeyboardButton("✏️ Renombrar", callback_data=f"config_rename_{task_id}")],
        [InlineKeyboardButton("🔙 Volver al Panel", callback_data="panel_show"), InlineKeyboardButton("🔥 Procesar Ahora", callback_data=f"task_queuesingle_{task_id}")]
    ])
    
    return InlineKeyboardMarkup(keyboard)

def build_quality_menu(task_id: str) -> InlineKeyboardMarkup:
    """Construye el menú para seleccionar la calidad de un video."""
    keyboard = [
        [InlineKeyboardButton("Original", callback_data=f"set_quality_{task_id}_Original")],
        [InlineKeyboardButton("🎬 1080p", callback_data=f"set_quality_{task_id}_1080p")],
        [InlineKeyboardButton("🎬 720p", callback_data=f"set_quality_{task_id}_720p")],
        [InlineKeyboardButton("🎬 480p", callback_data=f"set_quality_{task_id}_480p")],
        [InlineKeyboardButton("🎬 360p", callback_data=f"set_quality_{task_id}_360p")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"task_process_{task_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)
    
def build_download_quality_menu(task_id: str, formats: list) -> InlineKeyboardMarkup:
    """Construye el menú de calidades de descarga para una URL."""
    keyboard = []
    
    video_formats = sorted([f for f in formats if f.get('vcodec') != 'none' and f.get('height')], key=lambda x: x.get('height', 0), reverse=True)
    audio_formats = sorted([f for f in formats if f.get('vcodec') == 'none' and f.get('abr')], key=lambda x: x.get('abr', 0), reverse=True)
    
    if video_formats:
        keyboard.append([InlineKeyboardButton("--- 🎬 Video ---", callback_data="noop")])
        for f in video_formats[:5]:
            label = f"{f.get('resolution', f.get('height', '...'))} ({f.get('ext')}) ~{format_bytes(f.get('filesize') or f.get('filesize_approx'))}".strip()
            keyboard.append([InlineKeyboardButton(label, callback_data=f"set_dlformat_{task_id}_{f.get('format_id')}")])
            
    if audio_formats:
        keyboard.append([InlineKeyboardButton("--- 🎵 Audio ---", callback_data="noop")])
        for f in audio_formats[:3]:
            label = f"Audio {f.get('acodec')} ~{int(f.get('abr',0))}k ~{format_bytes(f.get('filesize') or f.get('filesize_approx'))}".strip()
            keyboard.append([InlineKeyboardButton(label, callback_data=f"set_dlformat_{task_id}_{f.get('format_id')}")])
            
    keyboard.append([InlineKeyboardButton("🔙 Volver al Panel", callback_data="panel_show")])
    return InlineKeyboardMarkup(keyboard)

def build_search_results_keyboard(results: list) -> InlineKeyboardMarkup:
    """Construye el teclado para los resultados de búsqueda de música."""
    keyboard = []
    source_emojis = {"spotify": "🟢", "youtube": "🔴"}
    for res in results:
        res_id = str(res['_id'])
        source_emoji = source_emojis.get(res.get('source', 'youtube'), '❓')
        title = res.get('title', 'Título desconocido')
        artist = res.get('artist', 'Artista desconocido')
        
        display_text = f"{source_emoji} {title} - {artist}"
        short_text = (display_text[:60] + '...') if len(display_text) > 64 else display_text

        keyboard.append([InlineKeyboardButton(short_text, callback_data=f"song_select_{res_id}")])
    
    keyboard.append([InlineKeyboardButton("❌ Cancelar Búsqueda", callback_data="cancel_search")])
    return InlineKeyboardMarkup(keyboard)


def build_audio_convert_menu(task_id: str) -> InlineKeyboardMarkup:
    """Construye el menú para configurar la conversión de audio."""
    keyboard = [
        [
            InlineKeyboardButton("MP3", callback_data=f"set_audioprop_{task_id}_format_mp3"),
            InlineKeyboardButton("FLAC", callback_data=f"set_audioprop_{task_id}_format_flac"),
            InlineKeyboardButton("Opus", callback_data=f"set_audioprop_{task_id}_format_opus")
        ],
        [
            InlineKeyboardButton("128k", callback_data=f"set_audioprop_{task_id}_bitrate_128k"),
            InlineKeyboardButton("192k", callback_data=f"set_audioprop_{task_id}_bitrate_192k"),
            InlineKeyboardButton("320k", callback_data=f"set_audioprop_{task_id}_bitrate_320k")
        ],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"task_process_{task_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_audio_effects_menu(task_id: str, config: dict) -> InlineKeyboardMarkup:
    """Construye el menú para aplicar efectos de audio."""
    slowed = "✅" if config.get('slowed') else "❌"
    reverb = "✅" if config.get('reverb') else "❌"
    keyboard = [
        [InlineKeyboardButton(f"🐌 Slowed {slowed}", callback_data=f"set_audioeffect_{task_id}_slowed_toggle")],
        [InlineKeyboardButton(f"🌌 Reverb {reverb}", callback_data=f"set_audioeffect_{task_id}_reverb_toggle")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"task_process_{task_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_back_button(callback_data: str) -> InlineKeyboardMarkup:
    """Construye un simple teclado con un único botón de 'Volver'."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data=callback_data)]])