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


# YouTube/TikTok Handler (using yt-dlp)
async def get_ytdlp_info(url: str) -> Dict:
    """Extract video info using yt-dlp (works for YouTube, TikTok, and 1000+ sites)"""

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    }

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
    
    # Options for downloading
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            # Extract info to check the media
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'downloaded_media')
            
            # Create a temporary file for download
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp_file:
                # Update options for actual download to temp file
                download_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'outtmpl': tmp_file.name,
                }
                
                # Create a new YoutubeDL instance for download
                with yt_dlp.YoutubeDL(download_opts) as download_ydl:
                    download_ydl.download([url])
                
                # Read the file content
                with open(tmp_file.name, 'rb') as f:
                    content = f.read()
                
                # Clean up temp file
                os.unlink(tmp_file.name)
                
                return Response(
                    content=content,
                    media_type='video/mp4',
                    headers={
                        'Content-Disposition': f'attachment; filename="{title}.mp4"'
                    }
                )
        except Exception as e:
            # Clean up temp file if it exists in case of error
            if 'tmp_file' in locals():
                try:
                    os.unlink(tmp_file.name)
                except:
                    pass
            raise HTTPException(status_code=400, detail=f"Failed to download media: {str(e)}")


@app.post("/api/download_format")
async def download_format(request: FormatDownloadRequest):
    """Download specific format by format_id"""
    url = str(request.url)
    
    # Detect platform
    platform = detect_platform(url)
    if platform == 'unknown':
        raise HTTPException(status_code=400, detail="Unsupported platform")
    
    # Options for downloading specific format
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            # Extract info to check available formats
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'downloaded_media')
            
            # Prepare download options with specific format
            download_opts = {
                'quiet': True,
                'no_warnings': True,
                'outtmpl': '%(title)s.%(ext)s',
            }
            
            # If format_id is specified, add it to options
            if request.format_id:
                download_opts['format'] = request.format_id
            
            with tempfile.TemporaryDirectory() as tmp_dir:
                # Set output template to temp directory
                download_opts['outtmpl'] = os.path.join(tmp_dir, '%(title)s.%(ext)s')
                
                with yt_dlp.YoutubeDL(download_opts) as download_ydl:
                    # Get the actual file info after format selection
                    file_info = download_ydl.extract_info(url, download=True)
                    
                    # Find the downloaded file in the temp directory
                    for file in os.listdir(tmp_dir):
                        if file.startswith(file_info.get('title', 'downloaded_media')[:50]):  # Use first 50 chars to avoid very long names
                            file_path = os.path.join(tmp_dir, file)
                            file_ext = Path(file_path).suffix
                            
                            with open(file_path, 'rb') as f:
                                content = f.read()
                            
                            return Response(
                                content=content,
                                media_type=f'video/{file_ext[1:]}' if file_ext[1:] in ['mp4', 'webm', 'flv', 'avi', 'mov', 'mkv'] else 'application/octet-stream',
                                headers={
                                    'Content-Disposition': f'attachment; filename="{title}{file_ext}"'
                                }
                            )
                    
                    # If no file was found, raise an error
                    raise HTTPException(status_code=404, detail="Downloaded file not found")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to download media: {str(e)}")


# Run with: uvicorn main:app --reload --host 0.0.0.0 --port 8000