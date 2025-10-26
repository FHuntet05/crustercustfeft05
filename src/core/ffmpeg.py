# --- INICIO DEL ARCHIVO src/core/ffmpeg.py ---

import logging
import subprocess
import os
import json
from typing import List, Tuple, Dict, Optional

logger = logging.getLogger(__name__)

def get_media_info(file_path: str) -> dict:
    if not os.path.exists(file_path):
        logger.error(f"ffprobe no puede encontrar el archivo: {file_path}")
        return {}
    command = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", file_path]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
        return json.loads(result.stdout)
    except Exception as e:
        logger.error(f"No se pudo obtener info de {file_path}: {e}")
        return {}

def build_ffmpeg_command(
    task: Dict, input_path: str, output_path: str,
    watermark_path: Optional[str] = None, replace_audio_path: Optional[str] = None,
    audio_thumb_path: Optional[str] = None, subs_path: Optional[str] = None
) -> Tuple[List[List[str]], str]:
    
    config = task.get('processing_config', {})
    
    if config.get('extract_audio'):
        return _build_extract_audio_command(input_path, output_path)
    
    return _build_video_command(task, input_path, output_path, watermark_path, replace_audio_path, subs_path)

def _build_video_command(
    task: Dict, input_path: str, output_path: str, watermark_path: Optional[str],
    replace_audio_path: Optional[str], subs_path: Optional[str]
) -> Tuple[List[List[str]], str]:
    
    config = task.get('processing_config', {})
    command = ["ffmpeg", "-y", "-hide_banner"]

    # --- 1. Entradas (Inputs) ---
    if trim_times := config.get('trim_times'):
        try:
            if '-' in trim_times:
                start, end = trim_times.split('-', 1)
                command.extend(["-ss", start.strip(), "-to", end.strip()])
            else:
                command.extend(["-to", trim_times.strip()])
        except Exception as e:
            logger.warning(f"Formato de trim inválido, se ignorará: {e}")
    command.extend(["-i", input_path])

    input_map = {"video": "0"}
    input_count = 1
    if watermark_path:
        command.extend(["-i", watermark_path])
        input_map["watermark"] = str(input_count)
        input_count += 1
    if replace_audio_path:
        command.extend(["-i", replace_audio_path])
        input_map["audio"] = str(input_count)
        input_count += 1

    # --- 2. Filtros Complejos (Filter Complex) ---
    filter_complex_parts = []
    video_chain = f"[{input_map['video']}:v]"
    final_video_output_tag = "[v_out]" # Usaremos una etiqueta de salida final y consistente

    quality = config.get('quality')
    if quality:
        quality_map = {
            "1080p": ("1920:1080", "22"), "720p": ("1280:720", "24"),
            "480p": ("854:480", "26"),   "360p": ("640:360", "28")
        }
        if quality in quality_map:
            res, _ = quality_map[quality]
            scale_filter = f"scale={res}:force_original_aspect_ratio=decrease,pad={res}:(ow-iw)/2:(oh-ih)/2"
            filter_complex_parts.append(f"{video_chain}{scale_filter}[scaled_v]")
            video_chain = "[scaled_v]"

    if wm_conf := config.get('watermark'):
        pos_map = {'bottom_right': 'main_w-overlay_w-10:main_h-overlay_h-10'}
        position = pos_map.get(wm_conf.get('position', 'bottom_right'))
        if wm_conf.get('type') == 'image' and watermark_path:
            filter_complex_parts.append(f"{video_chain}[{input_map['watermark']}:v]overlay={position}[watermarked_v]")
            video_chain = "[watermarked_v]"
        elif wm_conf.get('type') == 'text':
            text = wm_conf.get('text', '').replace("'", "’")
            drawtext = f"drawtext=fontfile='assets/font.ttf':text='{text}':fontcolor=white@0.8:fontsize=24:box=1:boxcolor=black@0.5:boxborderw=5:{position}"
            filter_complex_parts.append(f"{video_chain}{drawtext}[watermarked_v]")
            video_chain = "[watermarked_v]"

    # [CORRECCIÓN CLAVE 1] Se aplica la etiqueta de salida final al último eslabón de la cadena de filtros.
    if filter_complex_parts:
        # Reemplaza la última etiqueta de salida (ej. '[scaled_v]') por la etiqueta final '[v_out]'
        last_filter = filter_complex_parts[-1]
        filter_complex_parts[-1] = last_filter.split('[')[-1].join(last_filter.rsplit(']', 1)) + final_video_output_tag
        command.extend(["-filter_complex", ";".join(filter_complex_parts)])

    # --- 3. Mapeo de Salidas (Mapping) ---
    if filter_complex_parts:
        command.extend(["-map", final_video_output_tag]) # Mapea siempre la etiqueta de salida final
    else:
        command.extend(["-map", f"{input_map['video']}:v?"]) # Si no hay filtros, mapea el video original

    if replace_audio_path:
        command.extend(["-map", f"{input_map['audio']}:a"])
    elif config.get('mute_audio'):
        command.append("-an")
    else:
        command.extend(["-map", f"{input_map['video']}:a?"])

    command.extend(["-map", f"{input_map['video']}:s?"])

    # --- 4. Opciones de Codificación (Encoding) ---
    # [CORRECCIÓN CLAVE 2] Si hay filtros, SIEMPRE se debe recodificar. No se puede usar 'copy'.
    if filter_complex_parts:
        crf = "23" # CRF por defecto
        if quality and quality in quality_map:
            _, crf = quality_map[quality]
        command.extend(["-c:v", "libx264", "-preset", "fast", "-crf", crf])
        command.extend(["-c:a", "aac", "-b:a", "128k"]) # También recodificar audio para compatibilidad
    else:
        # Si no hay filtros, podemos copiar los streams directamente (muy rápido)
        command.extend(["-c:v", "copy", "-c:a", "copy"])

    command.extend(["-c:s", "mov_text"]) # Siempre procesar subtítulos para compatibilidad
    command.extend(["-movflags", "+faststart"])
    command.extend(["-progress", "pipe:2", output_path])
    
    # [CORRECCIÓN CLAVE 3] Se elimina todo el bloque de validación erróneo que estaba aquí.
    
    return [command], output_path

def _build_extract_audio_command(input_path: str, output_path_base: str) -> Tuple[List[List[str]], str]:
    final_output_path = f"{os.path.splitext(output_path_base)[0]}.m4a"
    command = ["ffmpeg", "-y", "-i", input_path, "-vn", "-c:a", "copy", final_output_path]
    return [command], final_output_path