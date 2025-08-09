# --- START OF FILE src/core/ffmpeg.py ---

import logging
import subprocess
import os
import json
from typing import List, Tuple, Dict

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

def build_ffmpeg_command(task: Dict, input_path: str, output_path: str, watermark_path: str = None) -> Tuple[List[str], str]:
    config = task.get('processing_config', {})

    if config.get('extract_audio'):
        return build_extract_audio_command(input_path, output_path)

    command: List[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    
    command.extend(["-i", input_path])
    if watermark_path:
        command.extend(["-i", watermark_path])

    filter_complex_parts = []
    video_chain_out = "[out_v]"
    final_video_map = "0:v?" # Mapeo por defecto

    # --- Lógica de Filtros de Video ---
    video_filters = []
    current_video_chain = "[0:v]"

    if transcode := config.get('transcode'):
        if res := transcode.get('resolution'):
            video_filters.append(f"scale=-2:{res.replace('p', '')}")

    if wm_conf := config.get('watermark'):
        if wm_conf.get('type') == 'image' and watermark_path:
            pos_map = {'top_left': '10:10', 'top_right': 'W-w-10:10'} # ... etc
            video_filters.append(f"overlay={pos_map.get(wm_conf.get('position', 'top_right'))}")
            # La entrada del filtro overlay es más compleja
            filter_str = f"{current_video_chain}[1:v]{','.join(video_filters)}{video_chain_out}"
            filter_complex_parts.append(filter_str)
        else:
            if video_filters:
                filter_str = f"{current_video_chain}{','.join(video_filters)}{video_chain_out}"
                filter_complex_parts.append(filter_str)
    
    if filter_complex_parts:
        command.extend(["-filter_complex", ";".join(filter_complex_parts)])
        final_video_map = video_chain_out # Si hay filtros, el video debe salir de ellos

    # --- Mapeo Explícito y Correcto ---
    command.extend(["-map", final_video_map])
    command.extend(["-map", "0:a?"])  # Mapear siempre el audio si existe
    command.extend(["-map", "0:s?"])  # Mapear siempre los subtítulos si existen

    # --- Lógica de Códecs ---
    if config.get('transcode'):
        command.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"])
        command.extend(["-c:a", "aac", "-b:a", "128k"])
    else:
        command.extend(["-c:v", "copy"])
        command.extend(["-c:a", "copy"])
        
    command.extend(["-c:s", "mov_text"])

    if config.get('mute_audio'):
        # Sobrescribir opciones de audio si se pide silenciar
        command = [arg for arg in command if not arg.startswith(("-c:a", "-b:a"))]
        command.append("-an")
        # Quitar el mapeo de audio explícitamente
        command = [arg for arg in command if arg != "0:a?"]


    command.append(output_path)
    
    logger.info(f"Comando FFmpeg construido (como lista): {command}")
    return [command], output_path


def build_extract_audio_command(input_path: str, output_path_base: str) -> tuple[List[List[str]], str]:
    media_info = get_media_info(input_path)
    audio_stream = next((s for s in media_info.get('streams', []) if s.get('codec_type') == 'audio'), None)
    ext = ".m4a"
    if audio_stream and (codec_name := audio_stream.get('codec_name')):
        ext_map = {'mp3': '.mp3', 'aac': '.m4a', 'opus': '.opus', 'flac': '.flac'}
        ext = ext_map.get(codec_name, '.m4a')
    
    final_output_path = f"{os.path.splitext(output_path_base)[0]}{ext}"
    command = ["ffmpeg", "-y", "-i", input_path, "-vn", "-c:a", "copy", final_output_path]
    return [command], final_output_path