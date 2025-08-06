from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .utils import escape_html, format_bytes

def build_panel_keyboard(tasks: list) -> InlineKeyboardMarkup:
    keyboard, task_ids = [], [str(t['_id']) for t in tasks]
    for task in tasks:
        task_id, file_type = str(task.get('_id')), task.get('file_type', 'document')
        emoji = {'video': '🎬', 'audio': '🎵', 'document': '📄'}.get(file_type, '📁')
        display_name = task.get('original_filename') or task.get('url', 'Tarea de URL')
        short_name = (display_name[:35] + '...') if len(display_name) > 38 else display_name
        keyboard.append([InlineKeyboardButton(f"{emoji} {escape_html(short_name)}", callback_data=f"task_process_{task_id}")])
    if tasks:
        keyboard.append([
            InlineKeyboardButton("✨ Procesar en Lote", callback_data=f"bulk_start_{','.join(task_ids)}"),
            InlineKeyboardButton("💥 Limpiar Panel", callback_data="panel_delete_all")
        ])
    return InlineKeyboardMarkup(keyboard)

def build_processing_menu(task_id: str, file_type: str, task_config: dict, filename: str = "") -> InlineKeyboardMarkup:
    keyboard = []
    if file_type == 'video':
        quality_text = f"⚙️ Convertir ({task_config.get('quality', 'Original')})"
        mute_text = "🔇 Silenciar" if not task_config.get('mute_audio') else "🔊 Desilenciar"
        keyboard.extend([
            [InlineKeyboardButton(quality_text, callback_data=f"config_quality_{task_id}")],
            [InlineKeyboardButton("✂️ Cortar", callback_data=f"config_trim_{task_id}"), InlineKeyboardButton("🧩 Dividir", callback_data=f"config_split_{task_id}")],
            [InlineKeyboardButton("🎞️ a GIF", callback_data=f"config_gif_{task_id}"), InlineKeyboardButton("💧 Marca de Agua", callback_data=f"config_watermark_{task_id}")],
            [InlineKeyboardButton("🎵/📜 Pistas (Muxer)", callback_data=f"config_tracks_{task_id}")],
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
        [InlineKeyboardButton("🔙 Volver", callback_data="panel_show"), InlineKeyboardButton("✅ Enviar a Cola", callback_data=f"task_queue_{task_id}")]
    ])
    return InlineKeyboardMarkup(keyboard)

def build_quality_menu(task_id: str) -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton(f"🎬 {q}", callback_data=f"set_quality_{task_id}_{q}")] for q in ['1080p', '720p', '480p', '360p']]
    keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data=f"task_process_{task_id}")])
    return InlineKeyboardMarkup(keyboard)

def build_tracks_menu(task_id: str) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("Funcionalidad en desarrollo", callback_data="noop")],
        [InlineKeyboardButton("🔙 Volver", callback_data=f"task_process_{task_id}")],
    ]
    return InlineKeyboardMarkup(keyboard)
    
def build_download_quality_menu(task_id: str, formats: list) -> InlineKeyboardMarkup:
    keyboard = []
    video_formats = sorted([f for f in formats if f.get('vcodec') != 'none' and f.get('height')], key=lambda x: x.get('height', 0), reverse=True)
    audio_formats = sorted([f for f in formats if f.get('vcodec') == 'none' and f.get('abr')], key=lambda x: x.get('abr', 0), reverse=True)
    if video_formats:
        keyboard.append([InlineKeyboardButton("--- 🎬 Video ---", callback_data="noop")])
        for f in video_formats[:5]:
            label = f"{f.get('resolution', f.get('height', 0))}p ({f.get('ext')}) ~{format_bytes(f.get('filesize'))}".strip()
            keyboard.append([InlineKeyboardButton(label, callback_data=f"set_dlformat_{task_id}_{f.get('format_id')}")])
    if audio_formats:
        keyboard.append([InlineKeyboardButton("--- 🎵 Audio ---", callback_data="noop")])
        for f in audio_formats[:3]:
            label = f"Audio {f.get('acodec')} ~{int(f.get('abr',0))}k ~{format_bytes(f.get('filesize'))}".strip()
            keyboard.append([InlineKeyboardButton(label, callback_data=f"set_dlformat_{task_id}_{f.get('format_id')}")])
    keyboard.append([InlineKeyboardButton("🔙 Volver al Panel", callback_data="panel_show")])
    return InlineKeyboardMarkup(keyboard)

def build_audio_convert_menu(task_id: str) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("MP3", callback_data=f"set_audioprop_{task_id}_format_mp3"), InlineKeyboardButton("FLAC", callback_data=f"set_audioprop_{task_id}_format_flac"), InlineKeyboardButton("Opus", callback_data=f"set_audioprop_{task_id}_format_opus")],
        [InlineKeyboardButton("128k", callback_data=f"set_audioprop_{task_id}_bitrate_128k"), InlineKeyboardButton("192k", callback_data=f"set_audioprop_{task_id}_bitrate_192k"), InlineKeyboardButton("320k", callback_data=f"set_audioprop_{task_id}_bitrate_320k")],
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

def build_bulk_actions_menu(task_ids_str: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➡️ Convertir Videos a 720p", callback_data=f"bulk_action_convert720p_{task_ids_str}")],
        [InlineKeyboardButton("➡️ Renombrar en Lote", callback_data=f"bulk_action_rename_{task_ids_str}")],
        [InlineKeyboardButton("➡️ Unir Videos", callback_data=f"bulk_action_unify_{task_ids_str}")],
        [InlineKeyboardButton("➡️ Crear ZIP", callback_data=f"bulk_action_zip_{task_ids_str}")],
        [InlineKeyboardButton("🔙 Volver al Panel", callback_data="panel_show")],
    ])

def build_settings_menu(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Funcionalidad en desarrollo", callback_data="noop")]])

def build_song_results_keyboard(search_results: list) -> InlineKeyboardMarkup:
    keyboard = []
    for res in search_results:
        duration = int(res.get('duration', 0))
        duration_str = f"{duration // 60}:{str(duration % 60).zfill(2)}" if duration > 0 else ""
        label = f"• {duration_str} • {escape_html(res['title'])} — {escape_html(res['artist'])}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"song_select_{res['_id']}")])
    return InlineKeyboardMarkup(keyboard)

def build_back_button(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data=callback_data)]])