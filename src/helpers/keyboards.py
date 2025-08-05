import json
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from src.core import ffmpeg
from .utils import escape_html, format_bytes

# =================================================================
# 1. MENÚ DEL PANEL PRINCIPAL (/panel)
# =================================================================
def build_panel_keyboard(tasks: list) -> InlineKeyboardMarkup:
    """Construye el teclado para el panel de trabajo con las tareas pendientes."""
    keyboard = []
    task_ids = [str(t['_id']) for t in tasks]
    for task in tasks:
        task_id = str(task.get('_id'))
        
        file_type = task.get('file_type', 'document')
        emoji_map = {'video': '🎬', 'audio': '🎵', 'document': '📄'}
        emoji = emoji_map.get(file_type, '📁')

        display_name = task.get('original_filename') or task.get('url', 'Tarea de URL')
        short_name = (display_name[:35] + '...') if len(display_name) > 38 else display_name
        keyboard.append([InlineKeyboardButton(f"{emoji} {escape_html(short_name)}", callback_data=f"task_process_{task_id}")])
    
    if tasks:
        keyboard.append([
            InlineKeyboardButton("✨ Procesar en Lote (Bulk)", callback_data=f"bulk_start_{','.join(task_ids)}"),
            InlineKeyboardButton("💥 Limpiar Panel", callback_data="panel_delete_all")
        ])
    return InlineKeyboardMarkup(keyboard)

# =================================================================
# 2. MENÚ DE PROCESAMIENTO PRINCIPAL (POR TIPO DE ARCHIVO)
# =================================================================
def build_processing_menu(task_id: str, file_type: str, task_config: dict, filename: str = "") -> InlineKeyboardMarkup:
    """Construye el menú principal de acciones según el tipo de archivo."""
    keyboard = []
    
    if file_type == 'video':
        quality_text = f"⚙️ Convertir/Optimizar ({task_config.get('quality', 'Original')})"
        mute_text = "🔇 Silenciar Audio" if not task_config.get('mute_audio') else "🔊 Desilenciar Audio"
        keyboard.extend([
            [InlineKeyboardButton(quality_text, callback_data=f"config_quality_{task_id}")],
            [
                InlineKeyboardButton("✂️ Cortar", callback_data=f"config_trim_{task_id}"),
                InlineKeyboardButton("🧩 Dividir", callback_data=f"config_split_{task_id}")
            ],
            [
                InlineKeyboardButton("📸 Capturas", callback_data=f"config_screenshot_{task_id}"),
                InlineKeyboardButton("🎞️ a GIF", callback_data=f"config_gif_{task_id}")
            ],
            [InlineKeyboardButton("🎞️ Crear Muestra", callback_data=f"config_sample_{task_id}")],
            [InlineKeyboardButton("🎵/📜 Pistas (Muxer)", callback_data=f"config_tracks_{task_id}")],
            [InlineKeyboardButton(mute_text, callback_data=f"set_mute_{task_id}_toggle")],
            [InlineKeyboardButton("📄 Editar Caption/Botones", callback_data=f"config_caption_{task_id}")],
        ])

    elif file_type == 'audio':
        bitrate = task_config.get('audio_bitrate', '128k')
        audio_format = task_config.get('audio_format', 'mp3')
        keyboard.extend([
            [InlineKeyboardButton(f"🔊 Convertir ({audio_format.upper()}, {bitrate})", callback_data=f"config_audioconvert_{task_id}")],
            [InlineKeyboardButton("🎧 Efectos (EQ, Vel., etc.)", callback_data=f"config_audioeffects_{task_id}")],
            [InlineKeyboardButton("✂️ Cortar", callback_data=f"config_audiotrim_{task_id}")],
            [InlineKeyboardButton("🖼️ Editar Tags/Carátula", callback_data=f"config_audiotags_{task_id}")],
        ])

    elif file_type == 'document':
        ext = os.path.splitext(filename)[1].lower() if filename else ""
        if ext in ['.zip', '.rar', '.7z']:
            keyboard.append([InlineKeyboardButton("📦 Extraer Archivo", callback_data=f"config_extract_{task_id}")])
        elif ext in ['.srt', '.vtt', '.ass']:
            keyboard.append([InlineKeyboardButton("📜 Convertir Subtítulo a VTT", callback_data=f"set_subconvert_{task_id}_vtt")])
        else:
            keyboard.append([InlineKeyboardButton("ℹ️ Tipo de documento sin acciones especiales.", callback_data="noop")])

    keyboard.extend([
        [InlineKeyboardButton("✏️ Renombrar Archivo de Salida", callback_data=f"config_rename_{task_id}")],
        [
            InlineKeyboardButton("🔙 Volver al Panel", callback_data="panel_show"),
            InlineKeyboardButton("✅ Enviar a la Cola", callback_data=f"task_queue_{task_id}")
        ]
    ])
    return InlineKeyboardMarkup(keyboard)

# =================================================================
# 3. SUB-MENÚS DE CONFIGURACIÓN
# =================================================================
def build_quality_menu(task_id: str) -> InlineKeyboardMarkup:
    """Construye el menú para seleccionar la calidad de conversión de video."""
    qualities = ['1080p', '720p', '480p', '360p', '240p', '144p']
    keyboard = [[InlineKeyboardButton(f"🎬 {q}", callback_data=f"set_quality_{task_id}_{q}")] for q in qualities]
    keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data=f"task_process_{task_id}")])
    return InlineKeyboardMarkup(keyboard)
    
def build_download_quality_menu(task_id: str, formats: list) -> InlineKeyboardMarkup:
    """Construye el menú de selección de calidad para descargas desde URL."""
    keyboard = []
    video_formats = sorted([f for f in formats if f.get('vcodec') != 'none' and f.get('height')], key=lambda x: x.get('height', 0), reverse=True)
    audio_formats = sorted([f for f in formats if f.get('vcodec') == 'none' and f.get('abr')], key=lambda x: x.get('abr', 0), reverse=True)
    
    if video_formats:
        keyboard.append([InlineKeyboardButton("--- 🎬 Video ---", callback_data="noop")])
        for f in video_formats[:6]:
            res = f.get('resolution', f"{f.get('height')}p")
            size = f"~{format_bytes(f.get('filesize'))}" if f.get('filesize') else ""
            label = f"{res} ({f.get('ext')}) {size}".strip()
            keyboard.append([InlineKeyboardButton(label, callback_data=f"set_dlformat_{task_id}_{f.get('format_id')}")])

    if audio_formats:
        keyboard.append([InlineKeyboardButton("--- 🎵 Audio ---", callback_data="noop")])
        for f in audio_formats[:3]:
            bitrate = f"{int(f.get('abr'))}k" if f.get('abr') else ""
            size = f"~{format_bytes(f.get('filesize'))}" if f.get('filesize') else ""
            label = f"Audio {f.get('acodec')} {bitrate} {size}".strip()
            keyboard.append([InlineKeyboardButton(label, callback_data=f"set_dlformat_{task_id}_{f.get('format_id')}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Volver al Panel", callback_data="panel_show")])
    return InlineKeyboardMarkup(keyboard)

def build_tracks_menu(task_id: str, media_info: dict) -> InlineKeyboardMarkup:
    """Construye el menú para gestionar pistas de audio y subtítulos."""
    keyboard = [[InlineKeyboardButton("🔙 Volver", callback_data=f"task_process_{task_id}")]]
    streams = media_info.get('streams', [])
    
    audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
    subtitle_streams = [s for s in streams if s.get('codec_type') == 'subtitle']

    if audio_streams:
        keyboard.insert(0, [InlineKeyboardButton("--- Pistas de Audio ---", callback_data="noop")])
        for stream in audio_streams:
            idx = stream.get('index')
            lang = stream.get('tags', {}).get('language', 'unk')
            codec = stream.get('codec_name', '---')
            title = stream.get('tags', {}).get('title', f"Pista #{idx}")
            keyboard.insert(1, [
                InlineKeyboardButton(f"🎵 {escape_html(title)} ({lang}, {codec})", callback_data="noop"),
                InlineKeyboardButton("🗑️ Eliminar", callback_data=f"set_trackop_{task_id}_remove_a_{idx}"),
            ])
    keyboard.insert(len(audio_streams) + 1, [InlineKeyboardButton("➕ Añadir Pista de Audio", callback_data=f"config_addtrack_{task_id}_audio")])
    
    if subtitle_streams:
        keyboard.append([InlineKeyboardButton("--- Pistas de Subtítulos ---", callback_data="noop")])
        for stream in subtitle_streams:
            idx = stream.get('index')
            lang = stream.get('tags', {}).get('language', 'unk')
            codec = stream.get('codec_name', '---')
            title = stream.get('tags', {}).get('title', f"Pista #{idx}")
            keyboard.append([
                InlineKeyboardButton(f"📜 {escape_html(title)} ({lang}, {codec})", callback_data="noop"),
                InlineKeyboardButton("🗑️ Eliminar", callback_data=f"set_trackop_{task_id}_remove_s_{idx}"),
            ])
    keyboard.append([InlineKeyboardButton("➕ Añadir Pista de Subtítulos", callback_data=f"config_addtrack_{task_id}_subtitle")])

    return InlineKeyboardMarkup(keyboard)

def build_audio_convert_menu(task_id: str) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("--- Formato ---", callback_data="noop")],
        [
            InlineKeyboardButton("MP3", callback_data=f"set_audioprop_{task_id}_format_mp3"),
            InlineKeyboardButton("FLAC (Sin pérdida)", callback_data=f"set_audioprop_{task_id}_format_flac"),
            InlineKeyboardButton("Opus (Eficiente)", callback_data=f"set_audioprop_{task_id}_format_opus")
        ],
        [InlineKeyboardButton("--- Bitrate (para MP3/Opus) ---", callback_data="noop")],
        [
            InlineKeyboardButton("128k", callback_data=f"set_audioprop_{task_id}_bitrate_128k"),
            InlineKeyboardButton("192k", callback_data=f"set_audioprop_{task_id}_bitrate_192k"),
            InlineKeyboardButton("320k", callback_data=f"set_audioprop_{task_id}_bitrate_320k")
        ],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"task_process_{task_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_audio_effects_menu(task_id: str, config: dict) -> InlineKeyboardMarkup:
    slowed = "✅" if config.get('slowed') else "❌"
    reverb = "✅" if config.get('reverb') else "❌"
    keyboard = [
        [InlineKeyboardButton(f"🐌 Slowed {slowed}", callback_data=f"set_audioeffect_{task_id}_slowed_toggle")],
        [InlineKeyboardButton(f"🌌 Reverb {reverb}", callback_data=f"set_audioeffect_{task_id}_reverb_toggle")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"task_process_{task_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

# =================================================================
# 4. MENÚS DE ACCIONES EN LOTE (BULK)
# =================================================================
def build_bulk_actions_menu(task_ids_str: str) -> InlineKeyboardMarkup:
    """Construye el menú de acciones para el modo Bulk."""
    keyboard = [
        [InlineKeyboardButton("➡️ Convertir Videos a MP4 720p", callback_data=f"bulk_action_convert720p_{task_ids_str}")],
        [InlineKeyboardButton("➡️ Renombrar en Lote", callback_data=f"bulk_action_rename_{task_ids_str}")],
        [InlineKeyboardButton("➡️ Unir Videos (En orden)", callback_data=f"bulk_action_unify_{task_ids_str}")],
        [InlineKeyboardButton("➡️ Crear ZIP con Todo", callback_data=f"bulk_action_zip_{task_ids_str}")],
        [InlineKeyboardButton("🔙 Volver al Panel", callback_data="panel_show")],
    ]
    return InlineKeyboardMarkup(keyboard)

# =================================================================
# 5. OTROS MENÚS
# =================================================================
def build_settings_menu(user_id: int) -> InlineKeyboardMarkup:
    """Construye el menú de configuración general del usuario."""
    # Placeholder - Se implementará en un lote futuro
    keyboard = [
        [InlineKeyboardButton("📝 Prefijo/Sufijo de Nombres", callback_data=f"settings_naming")],
        [InlineKeyboardButton("⚙️ Calidad de Video por Defecto", callback_data=f"settings_video_defaults")],
        [InlineKeyboardButton("🔗 Conectar Google Drive", callback_data=f"settings_gdrive_auth")],
        [InlineKeyboardButton("🔙 Volver (comando)", callback_data="noop")],
    ]
    return InlineKeyboardMarkup(keyboard)

def build_song_results_keyboard(search_results: list) -> InlineKeyboardMarkup:
    """Construye un teclado con los resultados de la búsqueda de música."""
    keyboard = []
    for i, res in enumerate(search_results):
        label = f"{i+1}. {escape_html(res['title'])} - {escape_html(res['artist'])}"
        # El callback data contiene el término de búsqueda para Youtube o la URL directa
        payload = res.get('url') or res.get('search_term')
        keyboard.append([InlineKeyboardButton(label, callback_data=f"song_download_{payload}")])
    return InlineKeyboardMarkup(keyboard)

# =================================================================
# 6. BOTONES GENÉRICOS
# =================================================================
def build_back_button(callback_data: str) -> InlineKeyboardMarkup:
    """Crea un teclado simple con un solo botón de 'Volver'."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data=callback_data)]])