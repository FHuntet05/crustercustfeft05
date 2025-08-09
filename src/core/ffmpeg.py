# --- START OF FILE src/core/ffmpeg.py ---

import logging
import subprocess
import shlex
import os
import json
from typing import List, Tuple

logger = logging.getLogger(__name__)

def get_media_info(file_path: str) -> dict:
    if not os.path.exists(file_path):
        logger.error(f"ffprobe no puede encontrar el archivo: {file_path}")
        return {}
        
    command = [
        "ffprobe", "-v", "error", "-show_format", "-show_streams",
        "-of", "json", file_path
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=True, timeout=60
        )
        return json.loads(result.stdout)
    except Exception as e:
        logger.error(f"No se pudo obtener info de {file_path}: {e}")
        return {}

def build_ffmpeg_command(task: dict, input_path: str, output_path: str, thumbnail_path: str = None, watermark_path: str = None, subs_path: str = None, new_audio_path: str = None) -> tuple[list[str], str]:
    config = task.get('processing_config', {})

    # Derivación a constructores de comandos especializados
    if config.get('extract_audio'):
        return build_extract_audio_command(input_path, output_path)

    # Constructor principal
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    
    # Manejo de tiempos (trim)
    if 'trim_times' in config:
        start, _, end = config['trim_times'].partition('-')
        if start.strip(): command.extend(["-ss", start.strip()])
        if end.strip(): command.extend(["-to", end.strip()])
    
    # Entradas principales y auxiliares
    command.extend(["-i", input_path])
    input_map_index = 1
    watermark_map_str, subs_map_str, thumb_map_str = "", "", ""
    if watermark_path:
        command.extend(["-i", watermark_path])
        watermark_map_str = f"[{input_map_index}:v]"
        input_map_index += 1
    if subs_path:
        command.extend(["-i", subs_path])
        subs_map_str = f"[{input_map_index}:s]"
        input_map_index += 1
    
    video_filters = []
    video_chain = "[0:v]"

    # Filtros de video (escala, texto de marca de agua)
    if task.get('file_type') == 'video':
        if transcode := config.get('transcode'):
            if res := transcode.get('resolution'):
                video_filters.append(f"scale=-2:{res.replace('p', '')}")
        
        if wm_conf := config.get('watermark'):
            if wm_conf.get('type') == 'text':
                text = wm_conf.get('text', '').replace("'", "’").replace(':', r'\:')
                pos_map = {'top_left': 'x=10:y=10', 'top_right': 'x=w-text_w-10:y=10', 'bottom_left': 'x=10:y=h-text_h-10', 'bottom_right': 'x=w-text_w-10:y=h-text_h-10'}
                video_filters.append(f"drawtext=text='{text}':fontcolor=white:fontsize=24:box=1:boxcolor=black@0.5:boxborderw=5:{pos_map.get(wm_conf.get('position', 'top_right'))}")

    # Aplicar filtros si existen
    if video_filters:
        video_chain_out = "[filtered_v]"
        command.extend(["-filter_complex", f"{video_chain}{','.join(video_filters)}{video_chain_out}"])
        video_chain = video_chain_out # La siguiente operación usará la salida de esta
        command.extend(["-map", video_chain])
    else:
        command.extend(["-map", "0:v?"])
        
    # Mapeo de audio y códecs
    if not config.get('mute_audio'):
        command.extend(["-map", "0:a?"])
        if config.get('transcode'):
            command.extend(["-c:a", "aac", "-b:a", "128k"])
        else:
            command.extend(["-c:a", "copy"])
    else:
        command.append("-an")
        
    # Códec de video
    if config.get('transcode'):
        command.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"])
    else:
        command.extend(["-c:v", "copy"])
        
    # Mapeo y códec de subtítulos
    command.extend(["-c:s", "mov_text", "-map", "0:s?"])
    
    command.append(output_path)
    
    final_command_str = shlex.join(command)
    logger.info(f"Comando FFmpeg construido: {final_command_str}")
    return [final_command_str], output_path

def build_extract_audio_command(input_path: str, output_path_base: str) -> tuple[List[str], str]:
    media_info = get_media_info(input_path)
    audio_stream = next((s for s in media_info.get('streams', []) if s.get('codec_type') == 'audio'), None)
    ext = ".m4a"
    if audio_stream and (codec_name := audio_stream.get('codec_name')):
        ext_map = {'mp3': '.mp3', 'aac': '.m4a', 'opus': '.opus', 'flac': '.flac'}
        ext = ext_map.get(codec_name, '.m4a')
    
    final_output_path = f"{os.path.splitext(output_path_base)[0]}{ext}"
    command = ["ffmpeg", "-y", "-i", input_path, "-vn", "-c:a", "copy", final_output_path]
    return [shlex.join(command)], final_output_path