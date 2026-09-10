import asyncio
import json
import os
import shutil
import subprocess
import logging

logger = logging.getLogger(__name__)

def get_binary(name: str) -> str:
    """Знаходить ffmpeg/ffprobe або в PATH, або в поточній директорії."""
    local_path = os.path.join(os.getcwd(), name)
    if os.path.isfile(local_path) and os.access(local_path, os.X_OK):
        return local_path
    which_path = shutil.which(name)
    if which_path:
        return which_path
    return name

FFMPEG_BIN = get_binary("ffmpeg")
FFPROBE_BIN = get_binary("ffprobe")

def get_media_info(file_path: str):
    """Отримує метадані відео через ffprobe."""
    cmd = [
        FFPROBE_BIN,
        "-v", "error",
        "-show_entries", "format=duration:stream=width,height,r_frame_rate,codec_type",
        "-of", "json",
        file_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    
    duration = float(data.get("format", {}).get("duration", 0.0))
    streams = data.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    width = int(video_stream.get("width", 1280))
    height = int(video_stream.get("height", 720))
    
    return {
        "duration": duration,
        "has_video": has_video,
        "has_audio": has_audio,
        "width": width,
        "height": height
    }

def calculate_insertion_time(duration: float) -> float:
    """
    Логіка вибору таймінгу:
    - Якщо тривалість >= 60 секунд: вставка рівно на 20-й секунді.
    - Якщо тривалість < 60 секунд: вставка рівно посередині (duration / 2).
    """
    if duration >= 60.0:
        return 20.0
    return duration / 2.0

async def process_video_with_banner(main_video_path: str, banner_video_path: str, output_path: str) -> str:
    """
    Накладає банер на головне відео з призупиненням (заморозкою кадру)
    на момент появи банера.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_process_video, main_video_path, banner_video_path, output_path)

def _sync_process_video(main_video_path: str, banner_video_path: str, output_path: str) -> str:
    main_info = get_media_info(main_video_path)
    banner_info = get_media_info(banner_video_path)
    
    main_dur = main_info["duration"]
    banner_dur = banner_info["duration"]
    
    if main_dur <= 0 or banner_dur <= 0:
        raise ValueError("Не вдалося визначити тривалість головного відео або банера.")
        
    t_insert = calculate_insertion_time(main_dur)
    if t_insert >= main_dur:
        t_insert = max(0.1, main_dur / 2.0)

    logger.info(f"Main video: {main_dur:.2f}s, Banner: {banner_dur:.2f}s, Insert at: {t_insert:.2f}s")
    
    main_has_audio = main_info["has_audio"]
    banner_has_audio = banner_info["has_audio"]
    
    filter_parts = []
    
    # 1. Головне відео: Частина 1 (від 0 до t_insert)
    filter_parts.append(f"[0:v]trim=start=0:end={t_insert:.3f},setpts=PTS-STARTPTS,fps=30,scale='iw-mod(iw,2)':'ih-mod(ih,2)'[v1]")
    if main_has_audio:
        filter_parts.append(f"[0:a]atrim=start=0:end={t_insert:.3f},asetpts=PTS-STARTPTS[a1]")
    else:
        filter_parts.append(f"aevalsrc=0:d={t_insert:.3f}[a1]")
        
    # 2. Заморожений кадр на момент t_insert тривалістю banner_dur
    freeze_end = min(main_dur, t_insert + 0.1)
    filter_parts.append(f"[0:v]trim=start={t_insert:.3f}:end={freeze_end:.3f},setpts=PTS-STARTPTS,fps=30,scale='iw-mod(iw,2)':'ih-mod(ih,2)'[freeze_slice]")
    filter_parts.append(f"[freeze_slice]tpad=stop_mode=clone:stop_duration={banner_dur + 0.1:.3f},trim=duration={banner_dur:.3f}[freeze_bg]")
    
    # 3. Масштабування банера відповідно до пропорцій головного відео
    filter_parts.append(f"[1:v]fps=30,scale=w='min(iw,{main_info['width']})':h='min(ih,{main_info['height']})':force_original_aspect_ratio=decrease[banner_scaled]")
    
    # 4. Накладання банера поверх замороженого кадру по центру
    filter_parts.append(f"[freeze_bg][banner_scaled]overlay=(W-w)/2:(H-h)/2:shortest=1[v_banner]")
    
    # 5. Аудіо банера
    if banner_has_audio:
        filter_parts.append(f"[1:a]atrim=start=0:end={banner_dur:.3f},asetpts=PTS-STARTPTS[a_banner]")
    else:
        filter_parts.append(f"aevalsrc=0:d={banner_dur:.3f}[a_banner]")
        
    # 6. Головне відео: Частина 2 (від t_insert до кінця)
    filter_parts.append(f"[0:v]trim=start={t_insert:.3f},setpts=PTS-STARTPTS,fps=30,scale='iw-mod(iw,2)':'ih-mod(ih,2)'[v2]")
    remaining_dur = max(0.01, main_dur - t_insert)
    if main_has_audio:
        filter_parts.append(f"[0:a]atrim=start={t_insert:.3f},asetpts=PTS-STARTPTS[a2]")
    else:
        filter_parts.append(f"aevalsrc=0:d={remaining_dur:.3f}[a2]")
        
    # 7. Конкатенація трьох відрізків
    filter_parts.append("[v1][a1][v_banner][a_banner][v2][a2]concat=n=3:v=1:a=1[outv][outa]")
    
    cmd = [
        FFMPEG_BIN,
        "-y",
        "-i", main_video_path,
        "-i", banner_video_path,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output_path
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"FFmpeg error: {result.stderr}")
        raise RuntimeError(f"Помилка обробки відео у FFmpeg: {result.stderr[-300:]}")
        
    return output_path
