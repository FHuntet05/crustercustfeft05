# --- START OF FILE src/core/ffmpeg.py ---

import logging
import subprocess
import os
import json
from typing import List, Tuple, Dict, Optional

logger = logging.getLogger(__name__)

def get_media_info(file_path: str) -> dict:
    """
    Obtiene metadatos de un archivo multimedia usando ffprobe.
    """
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

def build_ffmpeg_command(
    task: Dict,
    input_path: str,
    output_path: str,
    watermark_path: Optional[str] = None,
    subs_path: Optional[str] = None
) -> Tuple[List[List[str]], str]:
    """
    Función router principal que construye el comando FFmpeg apropiado
    basado en la configuración de la tarea.
    """
    config = task.get('processing_config', {})

    if config.get('extract_audio'):
        return _build_extract_audio_command(input_path, output_path)
    
    if config.get('gif_options'):
        return _build_gif_command(task, input_path, output_path)

    return _build_standard_video_command(task, input_path, output_path, watermark_path, subs_path)


def _build_standard_video_command(
    task: Dict,
    input_path: str,
    output_path: str,
    watermark_path: Optional[str],
    subs_path: Optional[str]
) -> Tuple[List[List[str]], str]:
    """
    Construye un comando FFmpeg para tareas de procesamiento de video estándar.
    """
    config = task.get('processing_config', {})
    command: List[str] = ["ffmpeg", "-y", "-hide_banner"]
    
    if trim_times := config.get('trim_times'):
        try:
            if '-' in trim_times:
                start, end = trim_times.split('-', 1)
                command.extend(["-ss", start.strip(), "-to", end.strip()])
            else:
                command.extend(["-to", trim_times.strip()])
        except Exception as e:
            logger.warning(f"Formato de trim inválido '{trim_times}': {e}. Se ignorará.")

    command.extend(["-i", input_path])
    if watermark_path:
        command.extend(["-i", watermark_path])
    if subs_path:
        command.extend(["-i", subs_path])

    filter_complex_parts = []
    current_video_chain = "[0:v]"

    video_filters = []
    if transcode := config.get('transcode'):
        if res := transcode.get('resolution'):
            video_filters.append(f"scale=-2:{res.replace('p', '')}")
    
    if video_filters:
        next_chain = "[scaled_v]"
        filter_str = f"{current_video_chain}{','.join(video_filters)}{next_chain}"
        filter_complex_parts.append(filter_str)
        current_video_chain = next_chain

    if watermark_config := config.get('watermark'):
        wm_type = watermark_config.get('type')
        position = watermark_config.get('position', 'bottom_right')
        next_chain = "[watermarked_v]"

        if wm_type == 'image' and watermark_path:
            pos_map = {
                'top_left': '10:10', 'top_right': 'main_w-overlay_w-10:10',
                'bottom_left': '10:main_h-overlay_h-10', 'bottom_right': 'main_w-overlay_w-10:main_h-overlay_h-10'
            }
            overlay_pos = pos_map.get(position, pos_map['bottom_right'])
            filter_str = f"{current_video_chain}[1:v]overlay={overlay_pos}{next_chain}"
            filter_complex_parts.append(filter_str)
            current_video_chain = next_chain

        elif wm_type == 'text':
            text_to_draw = watermark_config.get('text', '').replace("'", "’").replace(":", "∶")
            pos_map = {
                'top_left': 'x=10:y=10', 'top_right': 'x=w-text_w-10:y=10',
                'bottom_left': 'x=10:y=h-text_h-10', 'bottom_right': 'x=w-text_w-10:y=h-text_h-10'
            }
            text_pos = pos_map.get(position, pos_map['bottom_right'])
            
            # [IMPLEMENTACIÓN] Usar una fuente local del proyecto.
            # Asegúrate de tener un archivo de fuente en 'assets/font.ttf'.
            font_path = "assets/font.ttf"
            
            drawtext_filter = (
                f"drawtext=fontfile='{font_path}':"
                f"text='{text_to_draw}':fontcolor=white@0.8:fontsize=24:"
                f"box=1:boxcolor=black@0.5:boxborderw=5:{text_pos}"
            )
            
            filter_str = f"{current_video_chain}{drawtext_filter}{next_chain}"
            filter_complex_parts.append(filter_str)
            current_video_chain = next_chain

    if filter_complex_parts:
        command.extend(["-filter_complex", ";".join(filter_complex_parts)])
        final_video_map = current_video_chain.replace('[','').replace(']','')
        command.extend(["-map", f"[{final_video_map}]"])
    else:
        command.extend(["-map", "0:v?"])

    if config.get('mute_audio'):
        command.append("-an")
    else:
        command.extend(["-map", "0:a?"])

    if config.get('remove_subtitles'):
        command.append("-sn")
    elif subs_path:
        subs_input_index = "2" if watermark_path else "1"
        command.extend(["-map", f"{subs_input_index}:s?"])
    else:
        command.extend(["-map", "0:s?"])

    if config.get('transcode'):
        command.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"])
        command.extend(["-c:a", "aac", "-b:a", "128k"])
    else:
        command.extend(["-c:v", "copy"])
        command.extend(["-c:a", "copy"])
        
    command.extend(["-c:s", "mov_text"])
    command.extend(["-progress", "pipe:2"])
    command.append(output_path)

    return [command], output_path

def _build_extract_audio_command(input_path: str, output_path_base: str) -> Tuple[List[List[str]], str]:
    """Construye un comando FFmpeg para extraer la pista de audio sin recodificar."""
    media_info = get_media_info(input_path)
    audio_stream = next((s for s in media_info.get('streams', []) if s.get('codec_type') == 'audio'), None)
    ext = ".m4a"
    if audio_stream and (codec_name := audio_stream.get('codec_name')):
        ext_map = {'mp3': '.mp3', 'aac': '.m4a', 'opus': '.opus', 'flac': '.flac'}
        ext = ext_map.get(codec_name, '.m4a')
    
    final_output_path = f"{os.path.splitext(output_path_base)[0]}{ext}"
    command = ["ffmpeg", "-y", "-i", input_path, "-vn", "-c:a", "copy", final_output_path]
    return [command], final_output_path

def _build_gif_command(task: Dict, input_path: str, output_path_base: str) -> Tuple[List[List[str]], str]:
    """Construye un comando FFmpeg para crear un GIF de alta calidad a partir de un video."""
    config = task.get('processing_config', {})
    gif_opts = config.get('gif_options', {})
    duration = gif_opts.get('duration', 5.0)
    fps = gif_opts.get('fps', 15)
    
    final_output_path = f"{os.path.splitext(output_path_base)[0]}.gif"
    
    command: List[str] = ["ffmpeg", "-y"]

    if trim_times := config.get('trim_times'):
        if '-' in trim_times:
             start, _ = trim_times.split('-', 1)
             command.extend(["-ss", start.strip()])
        else:
            command.extend(["-ss", trim_times.strip()])

    command.extend(["-t", str(duration)])
    command.extend(["-i", input_path])
    
    filter_complex = (
        f"fps={fps},scale=480:-1:flags=lanczos,split[s0][s1];"
        f"[s0]palettegen[p];[s1][p]paletteuse"
    )
    
    command.extend(["-filter_complex", filter_complex])
    command.extend(["-progress", "pipe:2"])
    command.append(final_output_path)

    return [command], final_output_path