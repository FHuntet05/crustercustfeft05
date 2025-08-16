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
    Devuelve una tupla: (lista de comandos, ruta final del archivo de salida).
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
    Construye un comando FFmpeg para tareas de procesamiento de video estándar
    (transcodificación, corte, marca de agua, etc.).
    """
    config = task.get('processing_config', {})
    command: List[str] = ["ffmpeg", "-y", "-hide_banner"]
    
    # 1. Manejo del Corte (Trim) - Se aplica antes de la entrada para mayor eficiencia
    if trim_times := config.get('trim_times'):
        try:
            # Soportar formato "inicio-fin" o solo "fin"
            if '-' in trim_times:
                start, end = trim_times.split('-', 1)
                command.extend(["-ss", start.strip(), "-to", end.strip()])
            else:
                command.extend(["-to", trim_times.strip()])
        except Exception as e:
            logger.warning(f"Formato de trim inválido '{trim_times}': {e}. Se ignorará.")

    # 2. Definición de Entradas (Inputs)
    command.extend(["-i", input_path])
    if watermark_path:
        command.extend(["-i", watermark_path])
    if subs_path:
        command.extend(["-i", subs_path])

    # 3. Construcción de la Cadena de Filtros Complejos (`filter_complex`)
    filter_complex_parts = []
    current_video_chain = "[0:v]" # El stream de video del primer input

    # Filtros de video simples (ej. escalar)
    video_filters = []
    if transcode := config.get('transcode'):
        if res := transcode.get('resolution'):
            video_filters.append(f"scale=-2:{res.replace('p', '')}")
    
    if video_filters:
        next_chain = "[scaled_v]"
        filter_str = f"{current_video_chain}{','.join(video_filters)}{next_chain}"
        filter_complex_parts.append(filter_str)
        current_video_chain = next_chain

    # Filtro de superposición de Marca de Agua (overlay)
    if watermark_path:
        position = config.get('watermark', {}).get('position', 'bottom_right')
        pos_map = {
            'top_left': '10:10',
            'top_right': 'main_w-overlay_w-10:10',
            'bottom_left': '10:main_h-overlay_h-10',
            'bottom_right': 'main_w-overlay_w-10:main_h-overlay_h-10'
        }
        overlay_pos = pos_map.get(position, 'main_w-overlay_w-10:main_h-overlay_h-10')
        
        next_chain = "[watermarked_v]"
        # El stream [1:v] corresponde a la segunda entrada (-i watermark_path)
        filter_str = f"{current_video_chain}[1:v]overlay={overlay_pos}{next_chain}"
        filter_complex_parts.append(filter_str)
        current_video_chain = next_chain

    if filter_complex_parts:
        command.extend(["-filter_complex", ";".join(filter_complex_parts)])
        final_video_map = current_video_chain.replace('[','').replace(']','')
        command.extend(["-map", f"[{final_video_map}]"])
    else:
        command.extend(["-map", "0:v?"])

    # 4. Mapeo de Pistas de Audio y Subtítulos
    if config.get('mute_audio'):
        command.append("-an")  # Descartar todo el audio
    else:
        command.extend(["-map", "0:a?"]) # Mapear el audio del video original si existe

    if config.get('remove_subtitles'):
        command.append("-sn") # Descartar todos los subtítulos
    elif subs_path:
        command.extend(["-map", "2:s?"]) # Mapear los subtítulos del tercer input si existe
    else:
        command.extend(["-map", "0:s?"]) # Mapear los subtítulos originales si existen

    # 5. Configuración de Codecs
    if config.get('transcode'):
        command.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"])
        command.extend(["-c:a", "aac", "-b:a", "128k"])
    else:
        command.extend(["-c:v", "copy"])
        command.extend(["-c:a", "copy"])
        
    command.extend(["-c:s", "mov_text"])

    # 6. Finalización del Comando
    command.extend(["-progress", "pipe:2"])
    command.append(output_path)

    return [command], output_path

def _build_extract_audio_command(input_path: str, output_path_base: str) -> Tuple[List[List[str]], str]:
    """
    Construye un comando FFmpeg para extraer la pista de audio sin recodificar.
    """
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
    """
    Construye un comando FFmpeg para crear un GIF de alta calidad a partir de un video.
    """
    config = task.get('processing_config', {})
    gif_opts = config.get('gif_options', {})
    duration = gif_opts.get('duration', 5.0)
    fps = gif_opts.get('fps', 15)
    
    final_output_path = f"{os.path.splitext(output_path_base)[0]}.gif"
    
    command: List[str] = ["ffmpeg", "-y"]

    # Aplicar corte si está definido
    if trim_times := config.get('trim_times'):
        if '-' in trim_times:
             start, _ = trim_times.split('-', 1)
             command.extend(["-ss", start.strip()])
        else: # Si solo se especifica un tiempo, asumimos que es el inicio
            command.extend(["-ss", trim_times.strip()])

    command.extend(["-t", str(duration)]) # Duración del GIF
    command.extend(["-i", input_path])
    
    # Cadena de filtros complejos para la creación de GIF optimizado
    # 1. Ajusta FPS y escala el video.
    # 2. Divide el stream en dos: uno para el video, otro para generar la paleta.
    # 3. Genera una paleta de 256 colores.
    # 4. Usa la paleta generada para crear el GIF.
    filter_complex = (
        f"fps={fps},scale=480:-1:flags=lanczos,split[s0][s1];"
        f"[s0]palettegen[p];[s1][p]paletteuse"
    )
    
    command.extend(["-filter_complex", filter_complex])
    command.extend(["-progress", "pipe:2"])
    command.append(final_output_path)

    return [command], final_output_path