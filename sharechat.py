import os
import re
import json
import html
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ml;q=0.8",
}

def is_sharechat_url(url: str) -> bool:
    """Check if the URL belongs to ShareChat."""
    if not url:
        return False
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    return "sharechat.com" in domain or "sharechat" in domain

def get_sharechat_video_info(url: str) -> dict:
    """
    Extract direct video download link, title, duration, and thumbnail from a ShareChat URL.
    Returns dict: {'video_url': str, 'title': str, 'thumbnail': str, 'duration': int} or None.
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    
    try:
        response = session.get(url, timeout=15, allow_redirects=True)
        response.encoding = "utf-8"
        if response.status_code != 200:
            logger.error(f"ShareChat request failed with status: {response.status_code}")
            return None
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        title = "ShareChat Video"
        thumbnail = None
        duration = 0
        video_url = None
        
        # 1. Extract title
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = og_title["content"].strip()
        elif soup.title and soup.title.string:
            title = soup.title.string.strip()
            
        # Clean title suffix (e.g. " | Download - Usha")
        title = re.sub(r"\s*\|\s*Download\s*-\s*.*$", "", title, flags=re.IGNORECASE).strip()
        
        # 2. Extract thumbnail
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content"):
            thumbnail = og_img["content"]
            
        # 3. Extract duration if present in meta
        og_duration = soup.find("meta", property="og:video:duration")
        if og_duration and og_duration.get("content"):
            try:
                duration = int(float(og_duration["content"]))
            except Exception:
                pass

        # 4. Find video URL
        # Check og:video
        og_video = soup.find("meta", property="og:video") or soup.find("meta", property="og:video:secure_url")
        og_video_type = soup.find("meta", property="og:video:type")
        
        candidate_url = og_video["content"] if og_video and og_video.get("content") else None
        
        # If og:video is an HTML video player page (e.g., sharechat.com/video/...)
        if candidate_url and ("sharechat.com/video" in candidate_url or (og_video_type and "html" in og_video_type.get("content", ""))):
            logger.info(f"Following ShareChat video page: {candidate_url}")
            v_res = session.get(candidate_url, timeout=15)
            v_res.encoding = "utf-8"
            v_soup = BeautifulSoup(v_res.text, "html.parser")
            
            # Find video tag (ShareChat uses <video id="video-player-0" src="...">)
            v_tag = v_soup.find("video", id="video-player-0") or v_soup.find("video")
            if v_tag and v_tag.get("src"):
                video_url = html.unescape(v_tag["src"])
            else:
                # Regex search in video page
                match = re.search(r'https://cdn[^\s"\'<>]+\.mp4[^\s"\'<>]*', v_res.text)
                if match:
                    video_url = html.unescape(match.group(0))
        elif candidate_url and (".mp4" in candidate_url or (og_video_type and "mp4" in og_video_type.get("content", ""))):
            video_url = html.unescape(candidate_url)
            
        # 5. Direct video tag in current page if not found yet
        if not video_url:
            v_tag = soup.find("video", id="video-player-0") or soup.find("video")
            if v_tag and v_tag.get("src"):
                video_url = html.unescape(v_tag["src"])
                
        # 6. Check Next.js / Svelte scripts or regex
        if not video_url:
            match = re.search(r'https://cdn[^\s"\'<>]+\.mp4[^\s"\'<>]*', response.text)
            if match:
                video_url = html.unescape(match.group(0))

        if video_url:
            return {
                "video_url": video_url,
                "title": title if title else "ShareChat Video",
                "thumbnail": thumbnail,
                "duration": duration
            }
            
    except Exception as e:
        logger.error(f"ShareChat extraction failed: {e}", exc_info=True)
        
    return None

def download_sharechat_video(url: str, output_path: str) -> dict:
    """Download ShareChat video to specified output path."""
    info = get_sharechat_video_info(url)
    if not info or not info.get("video_url"):
        raise ValueError("Could not extract video stream from ShareChat URL")
        
    video_url = info["video_url"]
    session = requests.Session()
    session.headers.update(HEADERS)
    
    # Download the real media stream
    with session.get(video_url, stream=True, timeout=30) as r:
        r.raise_for_status()
        content_type = r.headers.get("content-type", "").lower()
        if "text/html" in content_type:
            raise ValueError(f"Extracted URL returned HTML instead of video: {video_url}")
            
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
                    
    return {
        "file_path": output_path,
        "title": info.get("title", "ShareChat Video"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration", 0)
    }
