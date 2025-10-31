import glob
import os
import tempfile
from typing import Optional, List, Dict
from datetime import datetime
import logging

import httpx
import yt_dlp
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Response, BackgroundTasks
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Social Media Downloader API", version="2.0.0")

# CORS middleware for Android app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request Models
class DownloadRequest(BaseModel):
    url: HttpUrl
    quality: Optional[str] = "best"


class FormatDownloadRequest(BaseModel):
    url: HttpUrl
    format_id: Optional[str] = None


class PhotoDownloadRequest(BaseModel):
    url: HttpUrl
    quality: Optional[str] = "best"


class MediaInfo(BaseModel):
    platform: str
    title: str
    thumbnail: str
    duration: Optional[float]
    formats: List[Dict]
    author: Optional[str]


# Progress tracking
download_progress = {}


def progress_hook(d):
    """Progress callback for yt-dlp"""
    if d['status'] == 'downloading':
        percent = d.get('_percent_str', 'N/A')
        speed = d.get('_speed_str', 'N/A')
        eta = d.get('_eta_str', 'N/A')
        logger.info(f"📥 Download progress: {percent} at {speed} (ETA: {eta})")
    elif d['status'] == 'finished':
        logger.info(f"✅ Download completed: {d.get('filename', 'unknown')}")
    elif d['status'] == 'error':
        logger.error(f"❌ Download error: {d.get('error', 'unknown error')}")


# Platform Detection
def detect_platform(url: str) -> str:
    """Detect which platform the URL belongs to"""
    url_lower = url.lower()

    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'youtube'
    elif 'tiktok.com' in url_lower:
        return 'tiktok'
    elif 'instagram.com' in url_lower:
        return 'instagram'
    elif 'facebook.com' in url_lower or 'fb.watch' in url_lower:
        return 'facebook'
    elif 'twitter.com' in url_lower or 'x.com' in url_lower:
        return 'twitter'
    elif 'linkedin.com' in url_lower:
        return 'linkedin'
    else:
        return 'unknown'


def get_ytdlp_options(extract_only=False, include_progress=False):
    """Get common yt-dlp options with proper configuration and timeouts"""
    options = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'nocheckcertificate': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'referer': 'https://www.youtube.com/',
        # Timeout configurations
        'socket_timeout': 30,
        'fragment_retries': 3,
        'retries': 3,
        'file_access_retries': 3,
        'extractor_retries': 3,
        # HTTP headers
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Sec-Fetch-Mode': 'navigate',
        },
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
                'player_skip': ['webpage', 'configs'],
            }
        },
    }

    if include_progress:
        options['progress_hooks'] = [progress_hook]
        options['quiet'] = False

    if not extract_only:
        options.update({
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'merge_output_format': 'mp4',
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
        })

    return options


def cleanup_temp_files(temp_dir: str):
    """Clean up temporary files and directory"""
    try:
        for file in glob.glob(os.path.join(temp_dir, '*')):
            try:
                os.unlink(file)
            except Exception as e:
                logger.warning(f"Failed to delete file {file}: {e}")
        os.rmdir(temp_dir)
        logger.info(f"🧹 Cleaned up temp directory: {temp_dir}")
    except Exception as e:
        logger.warning(f"Failed to cleanup temp directory {temp_dir}: {e}")


async def get_ytdlp_info(url: str) -> Dict:
    """Extract video info using yt-dlp"""
    ydl_opts = get_ytdlp_options(extract_only=True)

    try:
        logger.info(f"📊 Extracting info from: {url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            formats = []
            if 'formats' in info:
                for f in info['formats']:
                    if f.get('vcodec') != 'none':
                        formats.append({
                            'format_id': f.get('format_id'),
                            'quality': f.get('format_note', f.get('quality', 'unknown')),
                            'ext': f.get('ext'),
                            'filesize': f.get('filesize'),
                            'url': f.get('url'),
                            'height': f.get('height'),
                            'width': f.get('width'),
                            'fps': f.get('fps'),
                        })

            formats.sort(key=lambda x: x.get('height', 0) if x.get('height') else 0, reverse=True)

            logger.info(f"✅ Extracted info: {info.get('title')} ({len(formats)} formats)")

            return {
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'author': info.get('uploader'),
                'formats': formats[:10],
                'direct_url': info.get('url'),
            }
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        logger.error(f"❌ yt-dlp error: {error_msg}")
        if "403" in error_msg or "Forbidden" in error_msg:
            raise HTTPException(
                status_code=403,
                detail="Access denied - content may be private or geo-blocked"
            )
        elif "404" in error_msg or "not found" in error_msg.lower():
            raise HTTPException(
                status_code=404,
                detail="Content not found - URL may be invalid"
            )
        elif "timeout" in error_msg.lower():
            raise HTTPException(
                status_code=408,
                detail="Request timeout - please try again"
            )
        raise HTTPException(status_code=400, detail=f"Failed to extract info: {error_msg}")
    except Exception as e:
        logger.error(f"❌ Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")


async def get_instagram_info(url: str) -> Dict:
    """Extract Instagram media info"""
    try:
        return await get_ytdlp_info(url)
    except Exception:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers, follow_redirects=True)
            soup = BeautifulSoup(response.text, 'html.parser')

            og_video = soup.find('meta', property='og:video')
            og_image = soup.find('meta', property='og:image')
            og_title = soup.find('meta', property='og:title')

            return {
                'title': og_title['content'] if og_title else 'Instagram Post',
                'thumbnail': og_image['content'] if og_image else '',
                'formats': [{
                    'url': og_video['content'] if og_video else og_image['content'],
                    'quality': 'original',
                    'ext': 'mp4' if og_video else 'jpg'
                }]
            }


async def get_twitter_info(url: str) -> Dict:
    """Extract Twitter media info"""
    return await get_ytdlp_info(url)


async def get_facebook_info(url: str) -> Dict:
    """Extract Facebook video info"""
    return await get_ytdlp_info(url)


async def get_linkedin_info(url: str) -> Dict:
    """Extract LinkedIn media info"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')

        video_tag = soup.find('video')
        if video_tag and video_tag.get('src'):
            return {
                'title': 'LinkedIn Video',
                'thumbnail': video_tag.get('poster', ''),
                'formats': [{
                    'url': video_tag['src'],
                    'quality': 'original',
                    'ext': 'mp4'
                }]
            }
        else:
            raise HTTPException(status_code=404, detail="No video found in LinkedIn post")


# Main API Endpoints
@app.get("/")
async def root():
    return {
        "app": "Social Media Downloader API",
        "version": "2.0.0",
        "supported_platforms": ["YouTube", "TikTok", "Instagram", "Facebook", "Twitter", "LinkedIn"],
        "features": ["Streaming downloads", "Progress tracking", "Better error handling", "Timeout protection"]
    }


@app.get("/api/health")
async def health_check():
    """Enhanced health check with dependency status"""
    try:
        # Test yt-dlp
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            pass

        return {
            "status": "healthy",
            "yt_dlp": "working",
            "version": "2.0.0",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"❌ Health check failed: {str(e)}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@app.post("/api/extract", response_model=MediaInfo)
async def extract_media(request: DownloadRequest):
    """Extract media information from URL"""
    url = str(request.url)
    logger.info(f"🔍 Extract request for: {url}")

    platform = detect_platform(url)
    if platform == 'unknown':
        raise HTTPException(status_code=400, detail="Unsupported platform")

    handlers = {
        'youtube': get_ytdlp_info,
        'tiktok': get_ytdlp_info,
        'instagram': get_instagram_info,
        'twitter': get_twitter_info,
        'facebook': get_facebook_info,
        'linkedin': get_linkedin_info,
    }

    info = await handlers[platform](url)

    result = MediaInfo(
        platform=platform,
        title=info.get('title', 'Untitled'),
        thumbnail=info.get('thumbnail', ''),
        duration=info.get('duration'),
        formats=info.get('formats', []),
        author=info.get('author')
    )

    return result


@app.post("/api/download")
async def download_media_streaming(request: DownloadRequest, background_tasks: BackgroundTasks):
    """Download media file with streaming (memory efficient)"""
    url = str(request.url)
    logger.info(f"⬇️  Download request - URL: {url}, Quality: {request.quality}")

    platform = detect_platform(url)
    if platform == 'unknown':
        raise HTTPException(status_code=400, detail="Unsupported platform")

    temp_dir = tempfile.mkdtemp()
    logger.info(f"📁 Created temp directory: {temp_dir}")

    try:
        output_template = os.path.join(temp_dir, 'video')

        ydl_opts = get_ytdlp_options(include_progress=True)
        ydl_opts['outtmpl'] = output_template
        ydl_opts['noplaylist'] = True

        logger.info(f"🚀 Starting download with yt-dlp...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'downloaded_video')
            title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()
            logger.info(f"📝 Title: {title}")

        downloaded_files = glob.glob(os.path.join(temp_dir, 'video*'))
        if not downloaded_files:
            raise HTTPException(status_code=400, detail="No file was downloaded")

        downloaded_file = downloaded_files[0]
        file_size = os.path.getsize(downloaded_file)
        logger.info(f"📦 Downloaded file: {downloaded_file} ({file_size} bytes)")

        file_ext = os.path.splitext(downloaded_file)[1][1:] or 'mp4'

        mime_types = {
            'mp4': 'video/mp4',
            'webm': 'video/webm',
            'mkv': 'video/x-matroska',
            'avi': 'video/x-msvideo',
            'mov': 'video/quicktime',
            'flv': 'video/x-flv',
        }
        mime_type = mime_types.get(file_ext, 'video/mp4')

        # Stream the file in chunks
        def file_streamer():
            try:
                with open(downloaded_file, 'rb') as f:
                    while chunk := f.read(8192):  # 8KB chunks
                        yield chunk
                logger.info(f"✅ Streaming completed")
            except Exception as e:
                logger.error(f"❌ Streaming error: {str(e)}")
                raise

        # Schedule cleanup after streaming completes
        background_tasks.add_task(cleanup_temp_files, temp_dir)

        return StreamingResponse(
            file_streamer(),
            media_type=mime_type,
            headers={
                'Content-Disposition': f'attachment; filename="{title}.{file_ext}"',
                'Content-Length': str(file_size),
                'Accept-Ranges': 'bytes',
            }
        )

    except yt_dlp.utils.DownloadError as e:
        cleanup_temp_files(temp_dir)
        error_msg = str(e)
        logger.error(f"❌ yt-dlp error: {error_msg}")

        if "403" in error_msg or "Forbidden" in error_msg:
            raise HTTPException(
                status_code=403,
                detail="Access denied - content may be private or geo-blocked"
            )
        elif "timeout" in error_msg.lower():
            raise HTTPException(
                status_code=408,
                detail="Download timeout - please try again"
            )
        raise HTTPException(status_code=400, detail=f"Download failed: {error_msg}")

    except Exception as e:
        cleanup_temp_files(temp_dir)
        logger.error(f"❌ Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")


@app.post("/api/download_format")
async def download_format_streaming(request: FormatDownloadRequest, background_tasks: BackgroundTasks):
    """Download specific format with streaming"""
    url = str(request.url)
    logger.info(f"⬇️  Format download - URL: {url}, Format: {request.format_id}")

    temp_dir = tempfile.mkdtemp()

    try:
        output_template = os.path.join(temp_dir, 'video')

        download_opts = get_ytdlp_options(include_progress=True)
        download_opts['outtmpl'] = output_template
        download_opts['noplaylist'] = True

        if request.format_id:
            download_opts['format'] = request.format_id
            logger.info(f"🎯 Using specific format: {request.format_id}")
        else:
            download_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
            download_opts['merge_output_format'] = 'mp4'

        with yt_dlp.YoutubeDL(download_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'downloaded_video')
            title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()

        downloaded_files = glob.glob(os.path.join(temp_dir, 'video*'))
        if not downloaded_files:
            raise HTTPException(status_code=400, detail="No file was downloaded")

        downloaded_file = downloaded_files[0]
        file_size = os.path.getsize(downloaded_file)
        file_ext = os.path.splitext(downloaded_file)[1][1:] or 'mp4'

        mime_types = {
            'mp4': 'video/mp4',
            'webm': 'video/webm',
            'mkv': 'video/x-matroska',
            'm4a': 'audio/mp4',
            'mp3': 'audio/mpeg',
        }
        mime_type = mime_types.get(file_ext, 'video/mp4')

        def file_streamer():
            with open(downloaded_file, 'rb') as f:
                while chunk := f.read(8192):
                    yield chunk

        background_tasks.add_task(cleanup_temp_files, temp_dir)

        return StreamingResponse(
            file_streamer(),
            media_type=mime_type,
            headers={
                'Content-Disposition': f'attachment; filename="{title}.{file_ext}"',
                'Content-Length': str(file_size),
                'Accept-Ranges': 'bytes',
            }
        )

    except Exception as e:
        cleanup_temp_files(temp_dir)
        logger.error(f"❌ Download error: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Download failed: {str(e)}")


@app.post("/api/download_photo")
async def download_photo_streaming(request: PhotoDownloadRequest, background_tasks: BackgroundTasks):
    """Download photo with streaming"""
    url = str(request.url)
    logger.info(f"📸 Photo download - URL: {url}")

    platform = detect_platform(url)
    if platform == 'unknown':
        raise HTTPException(status_code=400, detail="Unsupported platform")

    temp_dir = tempfile.mkdtemp()

    try:
        output_template = os.path.join(temp_dir, 'photo')

        ydl_opts = get_ytdlp_options(include_progress=True)
        ydl_opts.update({
            'format': 'best',
            'outtmpl': output_template,
            'noplaylist': True,
            'skip_download': False,
            'writethumbnail': False,
        })

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get('title', 'downloaded_photo')
                title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()

            downloaded_files = glob.glob(os.path.join(temp_dir, 'photo*'))
            if downloaded_files:
                downloaded_file = downloaded_files[0]
            else:
                raise Exception("No file downloaded")

        except Exception:
            # Fallback to direct HTTP download
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                response = await client.get(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                soup = BeautifulSoup(response.text, 'html.parser')

                image_url = None
                og_image = soup.find('meta', property='og:image')
                if og_image and og_image.get('content'):
                    image_url = og_image['content']

                if not image_url:
                    raise HTTPException(status_code=404, detail="No image found")

                img_response = await client.get(image_url)
                if img_response.status_code != 200:
                    raise HTTPException(status_code=400, detail="Failed to download image")

                content_type = img_response.headers.get('content-type', '')
                ext = 'jpg'
                if 'png' in content_type:
                    ext = 'png'
                elif 'webp' in content_type:
                    ext = 'webp'

                downloaded_file = os.path.join(temp_dir, f'photo.{ext}')
                with open(downloaded_file, 'wb') as f:
                    f.write(img_response.content)

                title = "downloaded_photo"

        file_size = os.path.getsize(downloaded_file)
        file_ext = os.path.splitext(downloaded_file)[1][1:] or 'jpg'

        mime_types = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'webp': 'image/webp',
            'gif': 'image/gif',
        }
        mime_type = mime_types.get(file_ext.lower(), 'image/jpeg')

        def file_streamer():
            with open(downloaded_file, 'rb') as f:
                while chunk := f.read(8192):
                    yield chunk

        background_tasks.add_task(cleanup_temp_files, temp_dir)

        return StreamingResponse(
            file_streamer(),
            media_type=mime_type,
            headers={
                'Content-Disposition': f'attachment; filename="{title}.{file_ext}"',
                'Content-Length': str(file_size),
            }
        )

    except HTTPException:
        cleanup_temp_files(temp_dir)
        raise
    except Exception as e:
        cleanup_temp_files(temp_dir)
        logger.error(f"❌ Photo download error: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Failed to download photo: {str(e)}")


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request, exc: Exception):
    error_type = type(exc).__name__
    error_msg = str(exc)

    logger.error(f"🔥 Global error: {error_type} - {error_msg}")

    if "timeout" in error_msg.lower():
        return Response(
            status_code=408,
            content=f'{{"detail": "Download timeout - please try again"}}'
        )
    elif "403" in error_msg or "forbidden" in error_msg.lower():
        return Response(
            status_code=403,
            content=f'{{"detail": "Access denied - content may be private or geo-blocked"}}'
        )
    elif "404" in error_msg or "not found" in error_msg.lower():
        return Response(
            status_code=404,
            content=f'{{"detail": "Content not found - URL may be invalid"}}'
        )
    else:
        return Response(
            status_code=500,
            content=f'{{"detail": "Server error: {error_msg}"}}'
        )

# Run with: uvicorn main:app --reload --host 0.0.0.0 --port 8000