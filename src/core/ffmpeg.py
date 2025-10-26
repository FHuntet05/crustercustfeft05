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
    filter_graph = []
    video_label = f"[{input_map['video']}:v]"

    def _next_label(idx: int) -> str:
        return f"[v_step{idx}]"

    step_index = 0

    # Tabla de calidades aceptadas
    quality_map = {
        "1080p": ("1920:1080", "22"),
        "720p": ("1280:720", "24"),
        "480p": ("854:480", "26"),
        "360p": ("640:360", "28")
    }

    quality = config.get('quality')
    if quality and quality in quality_map:
        step_index += 1
        res, _ = quality_map[quality]
        dest_label = _next_label(step_index)
        scale_filter = (
            f"scale={res}:force_original_aspect_ratio=decrease,"
            f"pad={res}:(ow-iw)/2:(oh-ih)/2"
        )
        filter_graph.append((video_label, scale_filter, dest_label))
        video_label = dest_label

    if wm_conf := config.get('watermark'):
        pos_map = {
            'top_left': '10:10',
            'top_right': 'main_w-overlay_w-10:10',
            'bottom_left': '10:main_h-overlay_h-10',
            'bottom_right': 'main_w-overlay_w-10:main_h-overlay_h-10'
        }
        position = pos_map.get(wm_conf.get('position', 'bottom_right'))
        if wm_conf.get('type') == 'image' and watermark_path:
            step_index += 1
            dest_label = _next_label(step_index)
            left_inputs = f"{video_label}[{input_map['watermark']}:v]"
            filter_graph.append((left_inputs, f"overlay={position}", dest_label))
            video_label = dest_label
        elif wm_conf.get('type') == 'text':
            step_index += 1
            dest_label = _next_label(step_index)
            safe_text = wm_conf.get('text', '').replace("'", "’").replace(':', '∶')
            drawtext = (
                "drawtext=fontfile='assets/font.ttf':"
                f"text='{safe_text}':fontcolor=white@0.8:"
                "fontsize=24:box=1:boxcolor=black@0.5:boxborderw=5:"
                "x=(w-text_w-10):y=(h-text_h-10)"
            )
            filter_graph.append((video_label, drawtext, dest_label))
            video_label = dest_label

    final_video_label = video_label

    if filter_graph:
        # Forzamos que la última etapa genere una etiqueta conocida
        final_video_label = "[v_out]"
        src, flt, _ = filter_graph[-1]
        filter_graph[-1] = (src, flt, final_video_label)
        graph_str = ";".join(f"{src}{flt}{dst}" for src, flt, dst in filter_graph)
        command.extend(["-filter_complex", graph_str])

    # --- 3. Mapeo de Salidas (Mapping) ---
    if filter_graph:
        command.extend(["-map", final_video_label])
    else:
        command.extend(["-map", f"{input_map['video']}:v?"])

    if replace_audio_path:
        command.extend(["-map", f"{input_map['audio']}:a"])
    elif config.get('mute_audio'):
        command.append("-an")
    else:
        command.extend(["-map", f"{input_map['video']}:a?"])

    command.extend(["-map", f"{input_map['video']}:s?"])

    # --- 4. Opciones de Codificación (Encoding) ---
    if filter_graph:
        crf_value = "23"
        if quality and quality in quality_map:
            _, crf_value = quality_map[quality]
        command.extend(["-c:v", "libx264", "-preset", "fast", "-crf", crf_value])
        command.extend(["-c:a", "aac", "-b:a", "128k"])
    else:
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