from fastapi import FastAPI, HTTPException, BackgroundTasks, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from typing import Optional, List, Dict
import yt_dlp
import re
import httpx
from bs4 import BeautifulSoup
import redis
import json
from datetime import timedelta
import tempfile
import os
from pathlib import Path
import glob

app = FastAPI(title="Social Media Downloader API", version="1.0.0")

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


class MediaInfo(BaseModel):
    platform: str
    title: str
    thumbnail: str
    duration: Optional[int]
    formats: List[Dict]
    author: Optional[str]


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


# Get common yt-dlp options with proper headers and cookies
def get_ytdlp_options(extract_only=False):
    """Get common yt-dlp options with proper configuration"""
    options = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'nocheckcertificate': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'referer': 'https://www.youtube.com/',
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
                'player_skip': ['webpage', 'configs'],
            }
        },
    }

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


# YouTube/TikTok Handler (using yt-dlp)
async def get_ytdlp_info(url: str) -> Dict:
    """Extract video info using yt-dlp (works for YouTube, TikTok, and 1000+ sites)"""

    ydl_opts = get_ytdlp_options(extract_only=True)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            # Extract available formats
            formats = []
            if 'formats' in info:
                for f in info['formats']:
                    if f.get('vcodec') != 'none':  # Only video formats
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

            # Sort by quality (height)
            formats.sort(key=lambda x: x.get('height', 0) if x.get('height') else 0, reverse=True)

            return {
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'author': info.get('uploader'),
                'formats': formats[:10],  # Top 10 quality options
                'direct_url': info.get('url'),
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract video info: {str(e)}")


# Instagram Handler
async def get_instagram_info(url: str) -> Dict:
    """Extract Instagram media info"""

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    try:
        # Try yt-dlp first (supports Instagram)
        return await get_ytdlp_info(url)
    except:
        # Fallback to custom scraping if needed
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, follow_redirects=True)
            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract meta tags
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


# Twitter Handler
async def get_twitter_info(url: str) -> Dict:
    """Extract Twitter media info"""
    try:
        # yt-dlp supports Twitter
        return await get_ytdlp_info(url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract Twitter media: {str(e)}")


# Facebook Handler
async def get_facebook_info(url: str) -> Dict:
    """Extract Facebook video info"""
    try:
        # yt-dlp supports Facebook
        return await get_ytdlp_info(url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract Facebook media: {str(e)}")


# LinkedIn Handler
async def get_linkedin_info(url: str) -> Dict:
    """Extract LinkedIn media info"""

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            soup = BeautifulSoup(response.text, 'html.parser')

            # Find video element
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
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract LinkedIn media: {str(e)}")


# Main API Endpoints
@app.get("/")
async def root():
    return {
        "app": "Social Media Downloader API",
        "version": "1.0.0",
        "supported_platforms": ["YouTube", "TikTok", "Instagram", "Facebook", "Twitter", "LinkedIn"]
    }


@app.post("/api/extract", response_model=MediaInfo)
async def extract_media(request: DownloadRequest):
    """Extract media information from URL"""

    url = str(request.url)

    # Detect platform
    platform = detect_platform(url)

    if platform == 'unknown':
        raise HTTPException(status_code=400, detail="Unsupported platform")

    # Extract based on platform
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


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
    }


@app.post("/api/download")
async def download_media(request: DownloadRequest):
    """Download media file directly"""
    url = str(request.url)

    # Detect platform
    platform = detect_platform(url)
    if platform == 'unknown':
        raise HTTPException(status_code=400, detail="Unsupported platform")

    # Create a unique temporary directory
    temp_dir = tempfile.mkdtemp()

    try:
        # Define output template without extension (yt-dlp will add it)
        output_template = os.path.join(temp_dir, 'video')

        # Options for downloading with enhanced YouTube support
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': output_template,
            'quiet': False,
            'no_warnings': False,
            'noplaylist': True,
            'merge_output_format': 'mp4',
            'nocheckcertificate': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'referer': 'https://www.youtube.com/',
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
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
        }

        # Download the video
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'downloaded_video')
            # Clean title for filename
            title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()

        # Find the downloaded file (yt-dlp may or may not add extension)
        downloaded_files = glob.glob(os.path.join(temp_dir, 'video*'))

        if not downloaded_files:
            raise HTTPException(status_code=400, detail="No file was downloaded")

        downloaded_file = downloaded_files[0]

        # Get file extension (handle case where file has no extension)
        file_ext = os.path.splitext(downloaded_file)[1][1:] if '.' in os.path.basename(downloaded_file) else 'mp4'

        # If no extension, assume mp4 and rename the file
        if not file_ext or file_ext == '':
            file_ext = 'mp4'

        # Determine MIME type
        mime_types = {
            'mp4': 'video/mp4',
            'webm': 'video/webm',
            'mkv': 'video/x-matroska',
            'avi': 'video/x-msvideo',
            'mov': 'video/quicktime',
            'flv': 'video/x-flv',
        }
        mime_type = mime_types.get(file_ext, 'video/mp4')

        # Read the file
        if os.path.exists(downloaded_file) and os.path.getsize(downloaded_file) > 0:
            with open(downloaded_file, 'rb') as f:
                content = f.read()

            return Response(
                content=content,
                media_type=mime_type,
                headers={
                    'Content-Disposition': f'attachment; filename="{title}.{file_ext}"',
                    'Content-Length': str(len(content))
                }
            )
        else:
            raise HTTPException(status_code=400, detail="Downloaded file is empty or not created")

    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if "403" in error_msg or "Forbidden" in error_msg:
            raise HTTPException(
                status_code=400,
                detail="YouTube download blocked. Please update yt-dlp: pip install -U yt-dlp"
            )
        raise HTTPException(status_code=400, detail=f"yt-dlp download error: {error_msg}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to download media: {str(e)}")
    finally:
        # Clean up temp directory and all files in it
        try:
            for file in glob.glob(os.path.join(temp_dir, '*')):
                os.unlink(file)
            os.rmdir(temp_dir)
        except:
            pass


@app.post("/api/download_format")
async def download_format(request: FormatDownloadRequest):
    """Download specific format by format_id"""
    url = str(request.url)

    # Create a unique temporary directory
    temp_dir = tempfile.mkdtemp()

    try:
        # Define output template without extension
        output_template = os.path.join(temp_dir, 'video')

        # Prepare download options with specific format
        download_opts = {
            'outtmpl': output_template,
            'quiet': False,
            'no_warnings': False,
            'noplaylist': True,
            'nocheckcertificate': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'referer': 'https://www.youtube.com/',
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

        # If format_id is specified, use it; otherwise use best
        if request.format_id:
            download_opts['format'] = request.format_id
        else:
            download_opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
            download_opts['merge_output_format'] = 'mp4'

        with yt_dlp.YoutubeDL(download_opts) as download_ydl:
            # Get the info and download
            info = download_ydl.extract_info(url, download=True)
            title = info.get('title', 'downloaded_video')
            # Clean title for filename
            title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()

        # Find the downloaded file (may not have extension)
        downloaded_files = glob.glob(os.path.join(temp_dir, 'video*'))

        if not downloaded_files:
            raise HTTPException(status_code=400, detail="No file was downloaded")

        downloaded_file = downloaded_files[0]

        # Get file extension (handle case where file has no extension)
        file_ext = os.path.splitext(downloaded_file)[1][1:] if '.' in os.path.basename(downloaded_file) else 'mp4'

        # If no extension, assume mp4
        if not file_ext or file_ext == '':
            file_ext = 'mp4'

        # Determine MIME type
        mime_types = {
            'mp4': 'video/mp4',
            'webm': 'video/webm',
            'mkv': 'video/x-matroska',
            'avi': 'video/x-msvideo',
            'mov': 'video/quicktime',
            'flv': 'video/x-flv',
            'm4a': 'audio/mp4',
            'mp3': 'audio/mpeg',
        }
        mime_type = mime_types.get(file_ext, 'video/mp4')

        # Read and return the file
        if os.path.exists(downloaded_file) and os.path.getsize(downloaded_file) > 0:
            with open(downloaded_file, 'rb') as f:
                content = f.read()

            return Response(
                content=content,
                media_type=mime_type,
                headers={
                    'Content-Disposition': f'attachment; filename="{title}.{file_ext}"',
                    'Content-Length': str(len(content))
                }
            )
        else:
            raise HTTPException(status_code=400, detail="Downloaded file is empty or not created")

    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        if "403" in error_msg or "Forbidden" in error_msg:
            raise HTTPException(
                status_code=400,
                detail="YouTube download blocked. Please update yt-dlp: pip install -U yt-dlp"
            )
        raise HTTPException(status_code=400, detail=f"yt-dlp download error: {error_msg}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to download media: {str(e)}")
    finally:
        # Clean up temp directory and all files in it
        try:
            for file in glob.glob(os.path.join(temp_dir, '*')):
                os.unlink(file)
            os.rmdir(temp_dir)
        except:
            pass

# Run with: uvicorn main:app --reload --host 0.0.0.0 --port 8000