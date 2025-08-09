# --- START OF FILE src/core/ffmpeg.py ---

import asyncio
import logging
import subprocess
import shlex
import os
import re
import json

logger = logging.getLogger(__name__)

def get_media_info(file_path: str) -> dict:
    if not os.path.exists(file_path):
        logger.error(f"ffprobe no puede encontrar el archivo: {file_path}")
        return {}
        
    command = [
        "ffprobe", "-v", "error", "-show_format", "-show_streams",
        "-of", "default=noprint_wrappers=1", "-print_format", "json", file_path
    ]
    try:
        process = subprocess.Popen(
            command, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, encoding='utf-8'
        )
        stdout, stderr = process.communicate(timeout=60)
        
        if process.returncode != 0:
            logger.error(f"ffprobe falló para {file_path}. Error: {stderr.strip()}")
            return {}
            
        return json.loads(stdout)
    except Exception as e:
        logger.error(f"No se pudo obtener info de {file_path} (JSON o genérico): {e}")
        return {}

def build_ffmpeg_command(task: dict, input_path: str, output_path: str, thumbnail_path: str = None, watermark_path: str = None, subs_path: str = None, new_audio_path: str = None) -> tuple[list[str], str]:
    """
    Construye el comando FFmpeg principal.
    Devuelve: una tupla (lista_de_comandos, ruta_final_definitiva).
    """
    config = task.get('processing_config', {})

    if config.get('extract_audio'):
        return build_extract_audio_command(input_path, output_path, get_media_info(input_path))
    if config.get('replace_audio_file_id') and new_audio_path:
        return build_replace_audio_command(input_path, new_audio_path, output_path)
    if 'gif_options' in config:
        return build_gif_command(config, input_path, output_path)
    if 'split_criteria' in config:
        return build_split_command(config, input_path, output_path)

    command_parts = ["ffmpeg", "-y"]
    
    if 'trim_times' in config:
        start, _, end = config['trim_times'].partition('-')
        if start.strip(): command_parts.append(f"-ss {shlex.quote(start.strip())}")
        if end.strip(): command_parts.append(f"-to {shlex.quote(end.strip())}")
    
    command_parts.append(f"-i {shlex.quote(input_path)}")
    input_count = 1
    
    if thumbnail_path: command_parts.append(f"-i {shlex.quote(thumbnail_path)}"); thumb_map_idx = input_count; input_count += 1
    if watermark_path: command_parts.append(f"-i {shlex.quote(watermark_path)}"); watermark_map_idx = input_count; input_count += 1
    if subs_path: command_parts.append(f"-i {shlex.quote(subs_path)}"); subs_map_idx = input_count; input_count += 1

    codec_opts, map_opts, metadata_opts = [], [], []
    video_filters, filter_complex_parts = [], []
    video_chain = "[0:v]"
    
    if task.get('file_type') == 'video':
        if transcode_config := config.get('transcode'):
            if resolution := transcode_config.get('resolution'):
                video_filters.append(f"scale=-2:{resolution.replace('p', '')}")
    
    if watermark_config := config.get('watermark'):
        if watermark_config.get('type') == 'text':
            text = watermark_config.get('text', '').replace("'", "’").replace(':', r'\:')
            pos_map = {'top_left': 'x=10:y=10', 'top_right': 'x=w-text_w-10:y=10', 'bottom_left': 'x=10:y=h-text_h-10', 'bottom_right': 'x=w-text_w-10:y=h-text_h-10'}
            video_filters.append(f"drawtext=text='{text}':fontcolor=white:fontsize=24:box=1:boxcolor=black@0.5:boxborderw=5:{pos_map.get(watermark_config.get('position', 'top_right'))}")

    if video_filters:
        filter_complex_parts.append(f"{video_chain}{','.join(video_filters)}[filtered_v]")
        video_chain = "[filtered_v]"
    
    if config.get('watermark', {}).get('type') == 'image' and watermark_path:
        pos_map = {'top_left': '10:10', 'top_right': 'W-w-10:10', 'bottom_left': '10:H-h-10', 'bottom_right': 'W-w-10:H-h-10'}
        filter_complex_parts.append(f"{video_chain}[{watermark_map_idx}:v]overlay={pos_map.get(config['watermark'].get('position', 'top_right'))}[outv]")
        map_opts.append("-map [outv]")
    elif filter_complex_parts:
        last_filter = filter_complex_parts.pop()
        filter_complex_parts.append(last_filter.replace('[filtered_v]', '[outv]'))
        map_opts.append("-map [outv]")
    else:
        map_opts.append("-map 0:v:0?")

    if task.get('file_type') == 'video':
        codec_opts.extend(["-c:v copy", "-c:a copy"])
        if config.get('transcode'):
            codec_opts = ["-c:v libx264", "-preset veryfast", "-crf 28", "-c:a aac", "-b:a 128k"]
        
        codec_opts.append("-c:s mov_text")
        map_opts.append("-map 0:a:0?")
        
        if config.get('remove_thumbnail'): map_opts.append("-map -0:v:1") 
        elif thumbnail_path:
            map_idx_str = f"v:{len(map_opts)-2}" if "[outv]" not in map_opts else f"v:{len(map_opts)-1}"
            map_opts.append(f"-map {thumb_map_idx}")
            codec_opts.append(f"-c:{map_idx_str} mjpeg -disposition:{map_idx_str} attached_pic")

        if config.get('remove_subtitles'): map_opts.append("-map -0:s")
        else: map_opts.append("-map 0:s?")
        if subs_path: map_opts.append(f"-map {subs_map_idx}")
        map_opts.append("-map -0:t")
    
    elif task.get('file_type') == 'audio':
        fmt, bitrate = config.get('audio_format', 'mp3'), config.get('audio_bitrate', '192k')
        codec_map = {'mp3': 'libmp3lame', 'flac': 'flac', 'opus': 'libopus'}
        audio_filters = []
        if config.get('slowed'): audio_filters.append("atempo=0.8")
        if config.get('reverb'): audio_filters.append("aecho=0.8:0.9:1000:0.3")
        if audio_filters: codec_opts.append(f"-af {shlex.quote(','.join(audio_filters))}")
        codec_opts.extend(["-vn", f"-c:a {codec_map.get(fmt, 'libmp3lame')}"])
        if fmt != 'flac': codec_opts.append(f"-b:a {bitrate}")
        map_opts = ["-map 0:a:0"]
        if 'audio_tags' in config:
            tags = config['audio_tags']
            if tags.get('title'): metadata_opts.append(f"-metadata title={shlex.quote(tags['title'])}")
            if tags.get('artist'): metadata_opts.append(f"-metadata artist={shlex.quote(tags['artist'])}")
            if tags.get('album'): metadata_opts.append(f"-metadata album={shlex.quote(tags['album'])}")
        if thumbnail_path:
            map_opts.append(f"-map {thumb_map_idx}"); codec_opts.extend(["-c:v copy", "-disposition:v:0 attached_pic"])
    
    if config.get('mute_audio'):
        codec_opts = [opt for opt in codec_opts if '-c:a' not in opt and '-b:a' not in opt]
        map_opts = [opt for opt in map_opts if '0:a' not in opt]; codec_opts.append("-an")

    if filter_complex_parts: command_parts.append(f'-filter_complex "{";".join(filter_complex_parts)}"')

    command_parts.extend(codec_opts); command_parts.extend(metadata_opts); command_parts.extend(map_opts)
    command_parts.append(shlex.quote(output_path))
    
    final_command = " ".join(part for part in command_parts if part)
    logger.info(f"Comando FFmpeg estándar construido: {final_command}")
    return [final_command], output_path

def build_join_command(file_list_path: str, output_path: str) -> tuple[list[str], str]:
    command = f"ffmpeg -y -f concat -safe 0 -i {shlex.quote(file_list_path)} -c copy {shlex.quote(output_path)}"
    logger.info(f"Comando de unión de videos: {command}")
    return [command], output_path

def build_extract_audio_command(input_path: str, output_path_base: str, media_info: dict) -> tuple[list[str], str]:
    audio_stream = next((s for s in media_info.get('streams', []) if s.get('codec_type') == 'audio'), None)
    ext = ".m4a"
    if audio_stream:
        codec_name = audio_stream.get('codec_name')
        ext_map = {'mp3': '.mp3', 'aac': '.m4a', 'opus': '.opus', 'flac': '.flac'}
        ext = ext_map.get(codec_name, '.m4a')
    
    final_output_path = f"{os.path.splitext(output_path_base)[0]}{ext}"
    command = f"ffmpeg -y -i {shlex.quote(input_path)} -vn -c:a copy {shlex.quote(final_output_path)}"
    logger.info(f"Comando de extracción de audio: {command}")
    return [command], final_output_path

def build_replace_audio_command(input_path: str, new_audio_path: str, output_path: str) -> tuple[list[str], str]:
    command = (f"ffmpeg -y -i {shlex.quote(input_path)} -i {shlex.quote(new_audio_path)} "
               f"-map 0:v:0 -map 1:a:0 -c:v copy -c:a copy -map 0:s? -map -0:t "
               f"{shlex.quote(output_path)}")
    logger.info(f"Comando de reemplazo de audio: {command}")
    return [command], output_path

def build_extract_thumb_command(input_path: str, output_thumb_path: str) -> tuple[list[str], str]:
    command = (f"ffmpeg -y -i {shlex.quote(input_path)} -an -vf select='eq(pict_type,I)' "
               f"-vframes 1 -q:v 2 {shlex.quote(output_thumb_path)}")
    logger.info(f"Comando de extracción de miniatura: {command}")
    return [command], output_thumb_path

def build_gif_command(config, input_path, output_path) -> tuple[list[str], str]:
    gif_opts = config['gif_options']; duration, fps = gif_opts['duration'], gif_opts['fps']
    palette_path = f"{os.path.splitext(output_path)[0]}.palette.png"; filters = f"fps={fps},scale=480:-1:flags=lanczos"
    cmd1 = f"ffmpeg -y -ss 0 -t {duration} -i {shlex.quote(input_path)} -vf \"{filters},palettegen\" {shlex.quote(palette_path)}"
    final_gif_path = f"{os.path.splitext(output_path)[0]}.gif"
    cmd2 = f"ffmpeg -ss 0 -t {duration} -i {shlex.quote(input_path)} -i {shlex.quote(palette_path)} -lavfi \"{filters} [x]; [x][1:v] paletteuse\" {shlex.quote(final_gif_path)}"
    return [cmd1, cmd2], final_gif_path

def build_split_command(config, input_path, output_path) -> tuple[list[str], str]:
    criteria = config['split_criteria']; base_name, ext = os.path.splitext(output_path)
    final_output_pattern = f"{base_name}_part%03d{ext}"
    if 's' in criteria.lower():
        command = (f"ffmpeg -y -i {shlex.quote(input_path)} -c copy -map 0 "
                   f"-segment_time {criteria.lower().replace('s', '')} -f segment -reset_timestamps 1 {shlex.quote(final_output_pattern)}")
        # Para split, el "output path" es un patrón, no un archivo único. Devolvemos el patrón base para el glob.
        return [command], f"{base_name}_part*{ext}"
    return [""], ""