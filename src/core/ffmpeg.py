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
    
    # [CORREGIDO] La lógica ahora es más directa.
    config = task.get('processing_config', {})
    
    if config.get('extract_audio'):
        return _build_extract_audio_command(input_path, output_path)
    
    # Por ahora, nos enfocamos en el comando de video principal, que es el más complejo.
    return _build_video_command(task, input_path, output_path, watermark_path, replace_audio_path, subs_path)

def _build_video_command(
    task: Dict, input_path: str, output_path: str, watermark_path: Optional[str],
    replace_audio_path: Optional[str], subs_path: Optional[str]
) -> Tuple[List[List[str]], str]:
    
    config = task.get('processing_config', {})
    command = ["ffmpeg", "-y", "-hide_banner"]

    # --- 1. Manejo de Entradas (Inputs) ---
    # Input principal
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

    # Inputs adicionales
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
    # [FUTURO] Añadir manejo de miniaturas y subtítulos aquí si es necesario

    # --- 2. Construcción de Filtros Complejos (Filter Complex) ---
    filter_complex_parts = []
    video_chain = f"[{input_map['video']}:v]"

    # [CORREGIDO] Lógica de reescalado/compresión simplificada
    quality = config.get('quality')
    if quality:
        # Mapeo de calidades a resoluciones y CRF
        quality_map = {
            "1080p": ("1920:1080", "22"), "720p": ("1280:720", "24"),
            "480p": ("854:480", "26"),   "360p": ("640:360", "28")
        }
        if quality in quality_map:
            res, crf = quality_map[quality]
            scale_filter = f"scale={res}:force_original_aspect_ratio=decrease,pad={res}:(ow-iw)/2:(oh-ih)/2"
            filter_complex_parts.append(f"{video_chain}{scale_filter}[scaled_v]")
            video_chain = "[scaled_v]"

    # Lógica de marca de agua
    if wm_conf := config.get('watermark'):
        wm_type = wm_conf.get('type')
        pos_map = {
            'top_left': '10:10', 'top_right': 'main_w-overlay_w-10:10',
            'bottom_left': '10:main_h-overlay_h-10', 'bottom_right': 'main_w-overlay_w-10:main_h-overlay_h-10'
        }
        position = pos_map.get(wm_conf.get('position', 'bottom_right'))

        if wm_type == 'image' and watermark_path:
            filter_complex_parts.append(f"{video_chain}[{input_map['watermark']}:v]overlay={position}[watermarked_v]")
            video_chain = "[watermarked_v]"
        elif wm_type == 'text':
            text = wm_conf.get('text', '').replace("'", "’").replace(":", "∶")
            drawtext_filter = f"drawtext=fontfile='assets/font.ttf':text='{text}':fontcolor=white@0.8:fontsize=24:box=1:boxcolor=black@0.5:boxborderw=5:x=(w-text_w-10):y=(h-text_h-10)"
            filter_complex_parts.append(f"{video_chain}{drawtext_filter}[watermarked_v]")
            video_chain = "[watermarked_v]"

    if filter_complex_parts:
        command.extend(["-filter_complex", ";".join(filter_complex_parts)])

    # --- 3. Mapeo de Salidas (Mapping) ---
    command.extend(["-map", video_chain.strip("[]")]) # Mapea la última salida de la cadena de video

    if replace_audio_path:
        command.extend(["-map", f"{input_map['audio']}:a"])
    elif config.get('mute_audio'):
        command.append("-an") # No incluir audio
    else:
        command.extend(["-map", f"{input_map['video']}:a?"]) # Mapea el audio original si existe

    # [CORREGIDO] Siempre mantener los subtítulos originales a menos que se indique lo contrario
    if config.get('remove_subtitles'):
        command.append("-sn")
    else:
        command.extend(["-map", f"{input_map['video']}:s?"])

    # --- 4. Opciones de Codificación (Encoding) ---
    # [CORREGIDO] Aplicar compresión si se especificó una calidad
    if quality and quality in quality_map:
        res, crf = quality_map[quality]
        command.extend([
            "-c:v", "libx264", "-preset", "medium", "-crf", crf,
            "-c:a", "aac", "-b:a", "128k",
            "-c:s", "mov_text" # Codifica subtítulos para compatibilidad MP4
        ])
    else:
        # Si no se especifica calidad pero hay filtros, debemos recodificar.
        if filter_complex_parts:
             command.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "23"])
        else: # Si no hay filtros ni calidad, es una copia directa (muy rápido)
             command.extend(["-c:v", "copy"])
        # Siempre copiar audio y subtítulos si no se especifica lo contrario
        command.extend(["-c:a", "copy", "-c:s", "copy"])
        
    command.extend(["-movflags", "+faststart"])
    command.extend(["-progress", "pipe:2", output_path])
    
    # Validar entradas opcionales antes de construir el comando
    if watermark_path and not os.path.exists(watermark_path):
        raise ValueError(f"El archivo de marca de agua no existe: {watermark_path}")
    if replace_audio_path and not os.path.exists(replace_audio_path):
        raise ValueError(f"El archivo de audio para reemplazo no existe: {replace_audio_path}")

    # Validar que el mapa de entradas tenga las claves necesarias
    if 'video' not in input_map:
        raise ValueError("No se encontró un flujo de video en el mapa de entradas.")

    # Validar que los filtros complejos no estén vacíos si se especifican configuraciones
    if filter_complex_parts and not any(filter_complex_parts):
        raise ValueError("Los filtros complejos están vacíos a pesar de configuraciones activas.")

    # Validar que las opciones de calidad sean válidas
    if quality and quality not in quality_map:
        raise ValueError(f"La calidad especificada '{quality}' no es válida. Opciones disponibles: {list(quality_map.keys())}")

    # Validar que los mapas de salida sean correctos
    try:
        command.extend(["-map", video_chain.strip("[]")])
    except KeyError:
        raise ValueError("Error al mapear la salida de video. Verifique las configuraciones de entrada y filtros.")

    # Validar subtítulos si están habilitados
    if not config.get('remove_subtitles'):
        try:
            command.extend(["-map", f"{input_map['video']}:s?"])
        except KeyError:
            raise ValueError("Error al mapear subtítulos. Verifique las configuraciones de entrada.")

    # Validar que el flujo 'scaled_v' se haya generado correctamente
    if 'scaled_v' in video_chain:
        video_chain = video_chain.replace('scaled_v', 'scaled_v?')

    # Validar que el mapa de salida sea correcto
    if not video_chain.strip("[]"):
        raise ValueError("El mapa de salida de video está vacío. Verifique los filtros complejos.")

    # Actualizar el comando con el mapa de salida corregido
    command.extend(["-map", video_chain.strip("[]")])

    return [command], output_path

def _build_extract_audio_command(input_path: str, output_path_base: str) -> Tuple[List[List[str]], str]:
    # Esta función ya era correcta, la mantenemos.
    final_output_path = f"{os.path.splitext(output_path_base)[0]}.m4a"
    command = ["ffmpeg", "-y", "-i", input_path, "-vn", "-c:a", "copy", final_output_path]
    return [command], final_output_path