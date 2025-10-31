# Social Media Downloader API

A FastAPI application that allows downloading media content from various social media platforms including YouTube, TikTok, Instagram, Facebook, Twitter, and LinkedIn.

## Features

- Download videos from multiple platforms
- Extract media information (title, thumbnail, duration, formats)
- Streaming downloads (memory efficient)
- Progress tracking
- Better error handling
- Timeout protection

## Deployment to Render

This application can be easily deployed to Render using the following methods:

### Method 1: GitHub/GitLab Integration (Recommended)

1. **Push your code to a Git repository**:
   ```bash
   git init
   git add .
   git commit -m "Initial commit for Render deployment"
   git remote add origin <your-repository-url>
   git push -u origin main
   ```

2. **Create a new Web Service on Render**:
   - Go to https://dashboard.render.com/
   - Click "New +" and select "Web Service"
   - Connect your GitHub/GitLab account
   - Select your repository containing this code

3. **Configure your Web Service**:
   - Name: `social-media-downloader-api` (or your preferred name)
   - Environment: `Docker` (since we have a Dockerfile)
   - Branch: `main`
   - Environment Variables:
     - `PORT` = `10000` (Render will provide the actual port number)
   
4. **Deploy**: Click "Create Web Service" and Render will build and deploy your application automatically.

### Method 2: Using the render.yaml file

This repository includes a `render.yaml` file that defines the service configuration for Render. When you connect your repository to Render, it will automatically use this configuration.

## API Endpoints

- `GET /` - Root endpoint with app info
- `GET /api/health` - Health check endpoint
- `POST /api/extract` - Extract media information from a URL
- `POST /api/download` - Download media file with streaming
- `POST /api/download_format` - Download specific format with streaming
- `POST /api/download_photo` - Download photos with streaming

## Supported Platforms

- YouTube
- TikTok
- Instagram
- Facebook
- Twitter
- LinkedIn

## Requirements

- Python 3.8+
- Dependencies listed in `requirements.txt`
- FFmpeg (for video processing)

## Local Development

To run the application locally:

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`

## Docker Deployment

The application includes a Dockerfile that:
- Uses Python 3.11 slim image
- Installs FFmpeg for video processing
- Sets up the Python environment
- Runs the FastAPI application with uvicorn

## Important Notes for Render Deployment

1. Render automatically provides a `PORT` environment variable that your application should use
2. The application is configured to use this `PORT` environment variable
3. The Dockerfile installs all necessary dependencies including FFmpeg
4. The application uses streaming responses which is memory efficient for large file downloads
5. Temporary files are cleaned up after downloads to avoid storage issues on Render

## Security

- CORS is configured to allow all origins (you may want to restrict this in production)
- The API includes proper error handling and validation