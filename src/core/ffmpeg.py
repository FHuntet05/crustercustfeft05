import logging
import shlex
import os
import json
import subprocess
from typing import List, Tuple, Dict

from src.core.exceptions import FFmpegProcessingError

logger = logging.getLogger(__name__)

def get_media_info(file_path: str) -> dict:
    if not os.path.exists(file_path):
        return {}
    command = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", shlex.quote(file_path)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
        return json.loads(result.stdout)
    except Exception as e:
        logger.error(f"No se pudo obtener info de {file_path}: {e}")
        return {}

def build_command_for_task(task: Dict, input_paths: List[str], output_path: str, watermark_path: str = None) -> Tuple[List[str], str]:
    config = task.get('processing_config', {})
    file_type = task.get('file_type')

    if file_type == 'join_operation':
        return _build_join_command(input_paths, output_path)
    if file_type == 'zip_operation':
        return _build_zip_command(input_paths, output_path)
    
    input_path = input_paths[0] if input_paths else None
    if not input_path:
        raise ValueError("Se requiere una ruta de entrada para esta operación.")

    if config.get('extract_audio'):
        return _build_extract_audio_command(input_path, output_path)
    if 'gif_options' in config:
        return _build_gif_command(config, input_path, output_path)
    
    return _build_standard_ffmpeg_command(task, input_path, output_path, watermark_path)

def _build_standard_ffmpeg_command(task: Dict, input_path: str, output_path: str, watermark_path: str = None) -> Tuple[List[str], str]:
    config = task.get('processing_config', {})
    file_type = task.get('file_type')
    
    base_output_path, _ = os.path.splitext(output_path)
    if file_type == 'video':
        output_path = f"{base_output_path}.mp4"
    elif file_type == 'audio':
        output_path = f"{base_output_path}.{config.get('audio_format', 'mp3')}"
    
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    
    if trim_times := config.get('trim_times'):
        start, _, end = trim_times.partition('-')
        if start.strip():
            command.extend(["-ss", start.strip()])
    
    command.extend(["-i", shlex.quote(input_path)])
    
    if trim_times and end.strip():
        command.extend(["-to", end.strip()])

    # [CRITICAL FIX] Lógica de construcción de filter_complex reescrita para ser robusta.
    video_filters = []
    audio_filters = []
    filter_complex_chains = []
    
    # Definimos los streams de entrada iniciales
    video_input_stream = "[0:v]"
    audio_input_stream = "[0:a]"

    # 1. Aplicar filtros de video
    if file_type == 'video':
        if transcode_config := config.get('transcode'):
            if res := transcode_config.get('resolution'):
                video_filters.append(f"scale=-2:{res.replace('p', '')}")
        
        if watermark_config := config.get('watermark'):
            if watermark_config.get('type') == 'text':
                text = watermark_config.get('text', '').replace("'", "’").replace(':', r'\:').replace('%', r'\%').replace('"', '”')
                pos_map = {'top_left': 'x=10:y=10', 'top_right': 'x=w-text_w-10:y=10', 'bottom_left': 'x=10:y=h-text_h-10', 'bottom_right': 'x=w-text_w-10:y=h-text_h-10'}
                drawtext_position = pos_map.get(watermark_config.get('position', 'top_right'), 'x=w-text_w-10:y=h-text_h-10')
                video_filters.append(f"drawtext=text='{text}':fontcolor=white:fontsize=24:box=1:boxcolor=black@0.5:boxborderw=5:{drawtext_position}")

    # 2. Construir la cadena de filtros de video si hay alguno
    if video_filters:
        video_chain = f"{video_input_stream}{','.join(video_filters)}[v_filtered]"
        filter_complex_chains.append(video_chain)
        video_input_stream = "[v_filtered]" # El siguiente filtro usará la salida de este

    # 3. Aplicar marca de agua de imagen (que necesita su propia entrada)
    if file_type == 'video' and watermark_path and config.get('watermark', {}).get('type') == 'image':
        command.extend(["-i", shlex.quote(watermark_path)])
        pos_map = {'top_left': '10:10', 'top_right': 'W-w-10:10', 'bottom_left': '10:H-h-10', 'bottom_right': 'W-w-10:H-h-10'}
        overlay_position = pos_map.get(config['watermark'].get('position', 'top_right'), 'W-w-10:10')
        # El stream de video principal es el primer input, la marca de agua es el segundo '[1:v]'
        overlay_chain = f"{video_input_stream}[1:v]overlay={overlay_position}[v_watermarked]"
        filter_complex_chains.append(overlay_chain)
        video_input_stream = "[v_watermarked]"

    # 4. Aplicar filtros de audio
    if config.get('slowed'): audio_filters.append("atempo=0.8")
    if config.get('reverb'): audio_filters.append("aecho=0.8:0.9:1000:0.3")

    if audio_filters:
        audio_chain = f"{audio_input_stream}{','.join(audio_filters)}[a_filtered]"
        filter_complex_chains.append(audio_chain)
        audio_input_stream = "[a_filtered]"

    # 5. Aplicar el filter_complex si se construyó alguna cadena
    if filter_complex_chains:
        command.extend(["-filter_complex", ";".join(filter_complex_chains)])

    # 6. Mapear los streams de salida y definir codecs
    if file_type == 'video':
        command.extend(["-map", video_input_stream]) # Mapear el stream de video final
        if not config.get('mute_audio'):
            command.extend(["-map", audio_input_stream]) # Mapear el stream de audio final
        
        command.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"])
        if not config.get('mute_audio'):
            command.extend(["-c:a", "aac", "-b:a", "128k"])
        else:
            command.append("-an")
            
    elif file_type == 'audio':
        command.extend(["-map", audio_input_stream])
        fmt, bitrate = config.get('audio_format', 'mp3'), config.get('audio_bitrate', '192k')
        codec_map = {'mp3': 'libmp3lame', 'flac': 'flac', 'opus': 'libopus'}
        command.extend(["-c:a", codec_map.get(fmt, 'libmp3lame')])
        if fmt != 'flac':
            command.extend(["-b:a", bitrate])
        command.append("-vn")

    command.append(shlex.quote(output_path))
    
    final_command_str = shlex.join(command)
    logger.info(f"Comando FFmpeg construido: {final_command_str}")
    return [final_command_str], output_path


def _build_extract_audio_command(input_path: str, output_path_base: str) -> Tuple[List[str], str]:
    media_info = get_media_info(input_path)
    audio_stream = next((s for s in media_info.get('streams', []) if s.get('codec_type') == 'audio'), None)
    ext = ".m4a"
    if audio_stream and (codec_name := audio_stream.get('codec_name')):
        ext = {'mp3': '.mp3', 'aac': '.m4a', 'opus': '.opus', 'flac': '.flac'}.get(codec_name, '.m4a')
    final_output_path = f"{os.path.splitext(output_path_base)[0]}{ext}"
    command = f"ffmpeg -y -i {shlex.quote(input_path)} -vn -c:a copy {shlex.quote(final_output_path)}"
    return [command], final_output_path

def _build_gif_command(config: dict, input_path: str, output_path: str) -> Tuple[List[str], str]:
    gif_opts = config['gif_options']
    duration, fps = gif_opts.get('duration', 5), gif_opts.get('fps', 15)
    output_path = f"{os.path.splitext(output_path)[0]}.gif"
    palette_path = f"{os.path.splitext(output_path)[0]}.palette.png"
    filters = f"fps={fps},scale=480:-1:flags=lanczos"
    
    cmd1 = f"ffmpeg -y -ss 0 -t {duration} -i {shlex.quote(input_path)} -vf \"{filters},palettegen\" -y {shlex.quote(palette_path)}"
    cmd2 = f"ffmpeg -y -ss 0 -t {duration} -i {shlex.quote(input_path)} -i {shlex.quote(palette_path)} -lavfi \"{filters} [x]; [x][1:v] paletteuse\" -y {shlex.quote(output_path)}"
    
    return [cmd1, cmd2], output_path

def _build_join_command(input_files: List[str], output_path: str) -> Tuple[List[str], str]:
    base_output_path, _ = os.path.splitext(output_path)
    final_output_path = f"{base_output_path}.mp4"
    
    list_file_path = f"{base_output_path}_concat_list.txt"
    with open(list_file_path, 'w', encoding='utf-8') as f:
        for file_path in input_files:
            f.write(f"file {shlex.quote(file_path)}\n")
            
    command = f"ffmpeg -y -f concat -safe 0 -i {shlex.quote(list_file_path)} -c copy {shlex.quote(final_output_path)}"
    logger.info(f"Comando de unión construido. Usará el archivo de lista: {list_file_path}")
    
    return [command], final_output_path

def _build_zip_command(input_files: List[str], output_path: str) -> Tuple[List[str], str]:
    base_output_path, _ = os.path.splitext(output_path)
    final_output_path = f"{base_output_path}.zip"
    
    quoted_files = [shlex.quote(os.path.basename(f)) for f in input_files]
    # Cambiamos el directorio de trabajo para que los archivos en el zip no tengan la ruta completa
    work_dir = os.path.dirname(input_files[0])
    command = f"(cd {shlex.quote(work_dir)} && zip -j {shlex.quote(final_output_path)} {' '.join(quoted_files)})"
    logger.info("Comando de compresión ZIP construido.")
    
    return [command], final_output_path