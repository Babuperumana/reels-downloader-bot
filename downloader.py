import os
import re
import time
import json
import uuid
import logging
import subprocess
import requests
from pathlib import Path
from urllib.parse import urlparse
import yt_dlp
from yt_dlp.extractor.instagram import InstagramIE

import config
from sharechat import is_sharechat_url, download_sharechat_video

logger = logging.getLogger(__name__)

# Patch Instagram extractor to support photo posts seamlessly
_orig_ig_extract_product = InstagramIE._extract_product

def _custom_ig_extract_product(self, product_info, video_id, get_comments=False):
    res = _orig_ig_extract_product(self, product_info, video_id, get_comments)
    if not res.get("formats"):
        thumbs = res.get("thumbnails", [])
        if thumbs:
            best_thumb = thumbs[-1]["url"]
            res["formats"] = [{
                "url": best_thumb,
                "ext": "jpg",
                "format_id": "photo",
                "vcodec": "mjpeg",
                "acodec": "none",
                "width": thumbs[-1].get("width"),
                "height": thumbs[-1].get("height"),
            }]
            res["is_photo"] = True
    return res

InstagramIE._extract_product = _custom_ig_extract_product

URL_REGEX = re.compile(
    r'(https?://[^\s]+)',
    re.IGNORECASE
)

def clean_url(url: str) -> str:
    """Clean tracking and referral parameters from URLs."""
    if not url:
        return url
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    
    # Instagram: strip query params like ?utm_source=..., ?dlrf=...
    if "instagram.com" in domain or "instagr.am" in domain:
        match = re.search(r'/(reel|p|reels|tv)/([^/?&#]+)', parsed.path)
        if match:
            media_type, shortcode = match.groups()
            if media_type == 'reels':
                media_type = 'reel'
            return f"https://www.instagram.com/{media_type}/{shortcode}/"
            
    # YouTube: strip tracking
    elif "youtube.com" in domain or "youtu.be" in domain:
        if "youtu.be" in domain:
            video_id = parsed.path.lstrip('/')
            return f"https://youtu.be/{video_id}"
        elif "/shorts/" in parsed.path:
            match = re.search(r'/shorts/([^/?&#]+)', parsed.path)
            if match:
                return f"https://www.youtube.com/shorts/{match.group(1)}"
                
    return url

def extract_first_url(text: str) -> str | None:
    """Extract first valid http/https URL from text and clean tracking params."""
    if not text:
        return None
    match = URL_REGEX.search(text)
    if match:
        url = match.group(1).strip()
        url = url.rstrip(').,;!?\'"<>[]')
        return clean_url(url)
    return None

def detect_platform(url: str) -> str:
    """Detect platform from URL."""
    domain = urlparse(url).netloc.lower()
    if "instagram.com" in domain or "instagr.am" in domain:
        return "Instagram"
    elif "youtube.com" in domain or "youtu.be" in domain:
        return "YouTube"
    elif "facebook.com" in domain or "fb.watch" in domain or "fb.com" in domain:
        return "Facebook"
    elif is_sharechat_url(url):
        return "ShareChat"
    return "Other"

def get_media_metadata(file_path: str) -> dict:
    """Extract duration, width, height using ffprobe."""
    meta = {"duration": 0, "width": None, "height": None}
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-show_entries", "stream=width,height,duration",
            "-of", "json",
            file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            format_info = data.get("format", {})
            if "duration" in format_info and format_info["duration"]:
                try:
                    meta["duration"] = int(round(float(format_info["duration"])))
                except Exception:
                    pass
            
            for stream in data.get("streams", []):
                if stream.get("width") and stream.get("height"):
                    meta["width"] = int(stream["width"])
                    meta["height"] = int(stream["height"])
                if not meta["duration"] and stream.get("duration"):
                    try:
                        meta["duration"] = int(round(float(stream["duration"])))
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f"ffprobe extraction failed for {file_path}: {e}")
    return meta

def generate_thumbnail(video_path: str, thumb_path: str, fallback_url: str | None = None) -> str | None:
    """Generate JPEG thumbnail from video or fallback URL."""
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", "00:00:01",
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            thumb_path
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=10)
        if res.returncode != 0:
            cmd[2] = "00:00:00"
            res = subprocess.run(cmd, capture_output=True, timeout=10)
        if res.returncode == 0 and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except Exception as e:
        logger.warning(f"Thumbnail generation error: {e}")

    # Fallback to downloading image from URL if available
    if fallback_url:
        try:
            r = requests.get(fallback_url, timeout=10)
            if r.status_code == 200:
                with open(thumb_path, "wb") as f:
                    f.write(r.content)
                return thumb_path
        except Exception as e:
            logger.warning(f"Fallback thumbnail download error: {e}")
            
    return None

def convert_video_to_mp3(video_path: str, mp3_path: str) -> str:
    """Convert a video file to MP3 audio."""
    cmd = [
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "libmp3lame",
        "-ab", "192k",
        "-ar", "44100",
        mp3_path
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=60)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg MP3 conversion failed: {res.stderr.decode('utf-8', errors='ignore')}")
    return mp3_path

def compress_video_if_needed(video_path: str, max_bytes: int = config.MAX_FILESIZE_BYTES) -> str:
    """Compress video if it exceeds maximum size."""
    file_size = os.path.getsize(video_path)
    if file_size <= max_bytes:
        return video_path

    logger.info(f"File size {file_size} exceeds {max_bytes}, compressing...")
    meta = get_media_metadata(video_path)
    duration = meta.get("duration") or 30
    
    target_kbits = int((42 * 1024 * 8) / duration)
    video_bitrate = max(150, target_kbits - 96)
    
    compressed_path = str(video_path).replace(".mp4", "_compressed.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-c:v", "libx264",
        "-b:v", f"{video_bitrate}k",
        "-preset", "veryfast",
        "-c:a", "aac",
        "-b:a", "96k",
        compressed_path
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=120)
    if res.returncode == 0 and os.path.exists(compressed_path):
        os.remove(video_path)
        return compressed_path
    return video_path

def apply_watermark_to_video(video_path: str) -> str:
    """Overlays transparent watermark logo onto video with automatic relative scaling."""
    if not config.WATERMARK_ENABLED or not config.WATERMARK_FILE.exists():
        return video_path
        
    out_watermarked = str(video_path).replace(".mp4", "_wm.mp4")
    
    pos = getattr(config, "WATERMARK_POSITION", "top_right").lower()
    if pos == "top_left":
        overlay_expr = "25:25"
    elif pos == "bottom_right":
        overlay_expr = "main_w-overlay_w-25:main_h-overlay_h-50"
    elif pos == "bottom_left":
        overlay_expr = "25:main_h-overlay_h-50"
    else: # default top_right
        overlay_expr = "main_w-overlay_w-25:25"
        
    width_ratio = getattr(config, "WATERMARK_WIDTH_RATIO", 0.22)
    opacity = getattr(config, "WATERMARK_OPACITY", 0.75)
    
    filter_complex = (
        f"[1:v][0:v]scale2ref=w=main_w*{width_ratio}:h=-1[wm][base];"
        f"[wm]format=rgba,colorchannelmixer=aa={opacity}[wm_tr];"
        f"[base][wm_tr]overlay={overlay_expr}"
    )
    
    cmd = [
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-i", str(config.WATERMARK_FILE),
        "-filter_complex", filter_complex,
        "-c:a", "copy",
        "-c:v", "libx264",
        "-preset", "veryfast",
        out_watermarked
    ]
    
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=120)
        if res.returncode == 0 and os.path.exists(out_watermarked) and os.path.getsize(out_watermarked) > 0:
            try:
                os.remove(video_path)
            except Exception:
                pass
            return out_watermarked
        else:
            logger.warning(f"Watermark failed, keeping original: {res.stderr.decode('utf-8', errors='ignore')[:200]}")
    except Exception as e:
        logger.warning(f"Error applying watermark to video: {e}")
        
    return video_path

def apply_watermark_to_photo(photo_path: str) -> str:
    """Overlays transparent watermark logo onto photo."""
    if not config.WATERMARK_ENABLED or not config.WATERMARK_FILE.exists():
        return photo_path
        
    try:
        from PIL import Image
        base = Image.open(photo_path)
        logo = Image.open(config.WATERMARK_FILE).convert("RGBA")
        
        width_ratio = getattr(config, "WATERMARK_WIDTH_RATIO", 0.22)
        opacity = getattr(config, "WATERMARK_OPACITY", 0.75)
        
        target_w = max(50, int(base.width * width_ratio))
        target_h = int(logo.height * (target_w / logo.width))
        logo_resized = logo.resize((target_w, target_h), Image.Resampling.LANCZOS)
        
        r, g, b, a = logo_resized.split()
        a = a.point(lambda p: int(p * opacity))
        logo_resized.putalpha(a)
        
        pos_setting = getattr(config, "WATERMARK_POSITION", "top_right").lower()
        if pos_setting == "top_left":
            pos = (25, 25)
        elif pos_setting == "bottom_right":
            pos = (base.width - target_w - 25, base.height - target_h - 40)
        elif pos_setting == "bottom_left":
            pos = (25, base.height - target_h - 40)
        else: # top_right
            pos = (base.width - target_w - 25, 25)
            
        base_rgba = base.convert("RGBA")
        base_rgba.paste(logo_resized, pos, logo_resized)
        
        ext = os.path.splitext(photo_path)[1].lower()
        if ext in [".jpg", ".jpeg"]:
            result = base_rgba.convert("RGB")
            result.save(photo_path, quality=95)
        else:
            base_rgba.save(photo_path)
    except Exception as e:
        logger.warning(f"Error applying watermark to photo: {e}")
        
    return photo_path

def download_video(url: str) -> dict:
    """
    Downloads reel/video/photo for Instagram, YouTube, Facebook, ShareChat.
    """
    platform = detect_platform(url)
    uid = uuid.uuid4().hex[:8]
    
    # 1. Handle ShareChat
    if platform == "ShareChat":
        out_video = str(config.DOWNLOAD_DIR / f"sharechat_{uid}.mp4")
        thumb_path = str(config.DOWNLOAD_DIR / f"thumb_{uid}.jpg")
        try:
            info = download_sharechat_video(url, out_video)
            out_video = apply_watermark_to_video(out_video)
            meta = get_media_metadata(out_video)
            duration = meta.get("duration") or info.get("duration", 0)
            generated_thumb = generate_thumbnail(out_video, thumb_path, info.get("thumbnail"))
            out_video = compress_video_if_needed(out_video)
            return {
                "type": "video",
                "file_path": out_video,
                "title": info.get("title") or "ShareChat Reel",
                "duration": duration,
                "width": meta.get("width"),
                "height": meta.get("height"),
                "thumbnail": generated_thumb,
                "platform": "ShareChat",
                "file_size": os.path.getsize(out_video)
            }
        except Exception as e:
            logger.warning(f"ShareChat scraper failed: {e}, attempting yt-dlp fallback...")

    # 2. General yt-dlp downloader
    out_template = str(config.DOWNLOAD_DIR / f"{platform.lower()}_{uid}_%(id)s.%(ext)s")
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best/all",
        "merge_output_format": "mp4",
        "outtmpl": out_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
    }
    
    if config.HAS_COOKIES:
        ydl_opts["cookiefile"] = str(config.COOKIES_FILE)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        if not info_dict:
            raise ValueError("No media information could be retrieved.")
            
        if "entries" in info_dict and info_dict["entries"]:
            info_dict = info_dict["entries"][0]
            
        file_path = ydl.prepare_filename(info_dict)
        base, ext = os.path.splitext(file_path)
        if os.path.exists(f"{base}.mp4"):
            file_path = f"{base}.mp4"
            
        if not os.path.exists(file_path):
            matches = list(config.DOWNLOAD_DIR.glob(f"{platform.lower()}_{uid}_*"))
            if matches:
                file_path = str(matches[0])
            else:
                raise FileNotFoundError(f"Downloaded file not found for {url}")

    title = info_dict.get("title") or f"{platform} Media"
    
    # Check if downloaded media is a photo
    file_ext = os.path.splitext(file_path)[1].lower()
    is_photo = info_dict.get("is_photo") or file_ext in [".jpg", ".jpeg", ".png", ".webp"]
    
    if is_photo:
        file_path = apply_watermark_to_photo(file_path)
        return {
            "type": "photo",
            "file_path": file_path,
            "title": title,
            "platform": platform,
            "file_size": os.path.getsize(file_path)
        }

    # Video post-processing
    file_path = apply_watermark_to_video(file_path)
    thumb_path = str(config.DOWNLOAD_DIR / f"thumb_{uid}.jpg")
    meta = get_media_metadata(file_path)
    file_path = compress_video_if_needed(file_path)
    
    duration = meta.get("duration") or info_dict.get("duration") or 0
    if isinstance(duration, float):
        duration = int(round(duration))
        
    width = meta.get("width") or info_dict.get("width")
    height = meta.get("height") or info_dict.get("height")
    generated_thumb = generate_thumbnail(file_path, thumb_path, info_dict.get("thumbnail"))

    return {
        "type": "video",
        "file_path": file_path,
        "title": title,
        "duration": duration,
        "width": width,
        "height": height,
        "thumbnail": generated_thumb,
        "platform": platform,
        "file_size": os.path.getsize(file_path)
    }

def download_audio(url: str, existing_video_path: str | None = None) -> dict:
    """Downloads or extracts audio as MP3."""
    uid = uuid.uuid4().hex[:8]
    mp3_path = str(config.DOWNLOAD_DIR / f"audio_{uid}.mp3")
    platform = detect_platform(url)
    
    # Fast path: Convert existing video
    if existing_video_path and os.path.exists(existing_video_path):
        convert_video_to_mp3(existing_video_path, mp3_path)
        meta = get_media_metadata(mp3_path)
        return {
            "file_path": mp3_path,
            "title": "Audio",
            "duration": meta.get("duration", 0),
            "thumbnail": None,
            "platform": platform
        }

    # If ShareChat, download video first then convert
    if platform == "ShareChat":
        video_info = download_video(url)
        convert_video_to_mp3(video_info["file_path"], mp3_path)
        try:
            os.remove(video_info["file_path"])
        except Exception:
            pass
        meta = get_media_metadata(mp3_path)
        return {
            "file_path": mp3_path,
            "title": video_info.get("title") or "ShareChat Audio",
            "duration": meta.get("duration") or video_info.get("duration", 0),
            "thumbnail": video_info.get("thumbnail"),
            "platform": "ShareChat"
        }

    out_template = str(config.DOWNLOAD_DIR / f"audio_{uid}_%(id)s.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
    }
    
    if config.HAS_COOKIES:
        ydl_opts["cookiefile"] = str(config.COOKIES_FILE)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=True)
        if not info_dict:
            raise ValueError("No audio information retrieved.")
        if "entries" in info_dict and info_dict["entries"]:
            info_dict = info_dict["entries"][0]
            
        matches = list(config.DOWNLOAD_DIR.glob(f"audio_{uid}_*.mp3"))
        if matches:
            actual_mp3 = str(matches[0])
        else:
            raise FileNotFoundError(f"Extracted MP3 file not found for {url}")

    meta = get_media_metadata(actual_mp3)
    title = info_dict.get("title") or f"{platform} Audio"
    duration = meta.get("duration") or info_dict.get("duration") or 0
    if isinstance(duration, float):
        duration = int(round(duration))

    return {
        "file_path": actual_mp3,
        "title": title,
        "duration": duration,
        "thumbnail": None,
        "platform": platform
    }

def cleanup_files(*file_paths):
    """Safely delete temporary files."""
    for p in file_paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception as e:
                logger.warning(f"Error removing file {p}: {e}")

def auto_cleanup_downloads(max_age_hours: int = 6) -> dict:
    """
    Scans the downloads folder and deletes any files older than max_age_hours.
    Returns dict with count and bytes freed.
    """
    now = time.time()
    cutoff = now - (max_age_hours * 3600)
    count = 0
    bytes_freed = 0
    
    if config.DOWNLOAD_DIR.exists():
        for f in config.DOWNLOAD_DIR.iterdir():
            if f.is_file():
                try:
                    stat = f.stat()
                    if stat.st_mtime < cutoff:
                        bytes_freed += stat.st_size
                        f.unlink(missing_ok=True)
                        count += 1
                except Exception as e:
                    logger.warning(f"Error deleting old file {f}: {e}")
                    
    if count > 0:
        logger.info(f"Auto-cleanup: Removed {count} old files, freed {bytes_freed / (1024*1024):.2f} MB")
        
    return {"count": count, "bytes_freed": bytes_freed}

def clear_all_downloads() -> dict:
    """Deletes ALL files in downloads directory (manual trigger)."""
    count = 0
    bytes_freed = 0
    if config.DOWNLOAD_DIR.exists():
        for f in config.DOWNLOAD_DIR.iterdir():
            if f.is_file():
                try:
                    stat = f.stat()
                    bytes_freed += stat.st_size
                    f.unlink(missing_ok=True)
                    count += 1
                except Exception as e:
                    logger.warning(f"Error removing {f}: {e}")
    return {"count": count, "bytes_freed": bytes_freed}

