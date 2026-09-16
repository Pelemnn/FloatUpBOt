import asyncio
import json
import os
import shutil
import subprocess
import logging

logger = logging.getLogger(__name__)

def get_binary(name: str) -> str:
    """Знаходить ffmpeg/ffprobe в системі або поточній папці."""
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
    if not os.path.exists(file_path) or os.path.getsize(file_path) < 100:
        raise FileNotFoundError(f"Файл {file_path} відсутній або порожній.")
        
    cmd = [
        FFPROBE_BIN,
        "-v", "error",
        "-show_entries", "format=duration:stream=width,height,r_frame_rate,codec_type",
        "-of", "json",
        file_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout:
        raise ValueError(f"Не вдалося прочитати медіафайл: {result.stderr}")
        
    data = json.loads(result.stdout)
    
    duration = float(data.get("format", {}).get("duration", 0.0))
    streams = data.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    width = int(video_stream.get("width", 1280))
    height = int(video_stream.get("height", 720))
    
    # Робимо парні розміри (вимога x264)
    width = width if width % 2 == 0 else width - 1
    height = height if height % 2 == 0 else height - 1
    
    return {
        "duration": duration,
        "has_video": has_video,
        "has_audio": has_audio,
        "width": width,
        "height": height
    }

def calculate_insertion_time(duration: float) -> float:
    """
    Правило:
    - Якщо тривалість >= 60 сек -> вставка на 20-й секунді.
    - Якщо тривалість < 60 сек -> вставка рівно посередині.
    """
    if duration >= 60.0:
        return 20.0
    return max(0.5, duration / 2.0)

async def process_video_with_banner(main_video_path: str, banner_video_path: str, output_path: str) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_process_video, main_video_path, banner_video_path, output_path)

def _sync_process_video(main_video_path: str, banner_video_path: str, output_path: str) -> str:
    main_info = get_media_info(main_video_path)
    banner_info = get_media_info(banner_video_path)
    
    main_dur = main_info["duration"]
    banner_dur = banner_info["duration"]
    
    if main_dur <= 0 or banner_dur <= 0:
        raise ValueError("Тривалість відео або банера дорівнює 0.")
        
    t_insert = calculate_insertion_time(main_dur)
    if t_insert >= main_dur:
        t_insert = max(0.1, main_dur / 2.0)

    logger.info(f"Main video: {main_dur:.2f}s, Banner: {banner_dur:.2f}s, Insert at: {t_insert:.2f}s")
    
    main_has_audio = main_info["has_audio"]
    banner_has_audio = banner_info["has_audio"]
    
    w, h = main_info["width"], main_info["height"]
    
    filter_parts = []
    
    # 1. Частина 1 головного відео (0 -> t_insert)
    filter_parts.append(f"[0:v]trim=start=0:end={t_insert:.3f},setpts=PTS-STARTPTS,fps=30,scale={w}:{h}[v1]")
    if main_has_audio:
        filter_parts.append(f"[0:a]atrim=start=0:end={t_insert:.3f},asetpts=PTS-STARTPTS[a1]")
    else:
        filter_parts.append(f"aevalsrc=0:d={t_insert:.3f}[a1]")
        
    # 2. Заморозка кадру на момент t_insert
    freeze_end = min(main_dur, t_insert + 0.1)
    filter_parts.append(f"[0:v]trim=start={t_insert:.3f}:end={freeze_end:.3f},setpts=PTS-STARTPTS,fps=30,scale={w}:{h}[freeze_slice]")
    filter_parts.append(f"[freeze_slice]tpad=stop_mode=clone:stop_duration={banner_dur + 0.1:.3f},trim=duration={banner_dur:.3f}[freeze_bg]")
    
    # 3. Масштабування банера по центру
    filter_parts.append(f"[1:v]fps=30,scale=w='min(iw,{w})':h='min(ih,{h})':force_original_aspect_ratio=decrease[banner_scaled]")
    filter_parts.append(f"[freeze_bg][banner_scaled]overlay=(W-w)/2:(H-h)/2:shortest=1[v_banner]")
    
    # 4. Звук банера
    if banner_has_audio:
        filter_parts.append(f"[1:a]atrim=start=0:end={banner_dur:.3f},asetpts=PTS-STARTPTS[a_banner]")
    else:
        filter_parts.append(f"aevalsrc=0:d={banner_dur:.3f}[a_banner]")
        
    # 5. Частина 2 головного відео (t_insert -> кінець)
    filter_parts.append(f"[0:v]trim=start={t_insert:.3f},setpts=PTS-STARTPTS,fps=30,scale={w}:{h}[v2]")
    remaining_dur = max(0.01, main_dur - t_insert)
    if main_has_audio:
        filter_parts.append(f"[0:a]atrim=start={t_insert:.3f},asetpts=PTS-STARTPTS[a2]")
    else:
        filter_parts.append(f"aevalsrc=0:d={remaining_dur:.3f}[a2]")
        
    # 6. Конкатенація
    filter_parts.append("[v1][a1][v_banner][a_banner][v2][a2]concat=n=3:v=1:a=1[outv][outa]")
    
    # Стиснення для збереження маленького розміру файлу (-crf 27) та швидкої передачі в Telegram
    cmd = [
        FFMPEG_BIN,
        "-y",
        "-threads", "0",
        "-i", main_video_path,
        "-i", banner_video_path,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-tune", "fastdecode",
        "-crf", "27",
        "-c:a", "aac",
        "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output_path
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"FFmpeg error: {result.stderr}")
        raise RuntimeError(f"FFmpeg помилка: {result.stderr[-300:]}")
        
    return output_path
