"""VideoLinkSave: public-video extraction service; no accounts, cookies or DRM bypass."""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

app = FastAPI(title='VideoLinkSave API', version='2.0.0')
origins = [x.strip() for x in os.getenv('ALLOWED_ORIGINS', 'https://videolinksave.blogspot.com').split(',') if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                   allow_methods=['GET', 'POST'], allow_headers=['Content-Type'])
SLOTS = threading.BoundedSemaphore(2)
LIMIT = 300 * 1024 * 1024
EXTRACTORS = 'facebook.*,Facebook.*,Instagram.*,instagram.*,TikTok.*,tiktok.*,Twitter.*,twitter.*,SnapchatSpotlight'
UA = 'Mozilla/5.0'

class Link(BaseModel):
    url: str = Field(min_length=10, max_length=2000)


def classify(value):
    try:
        u = urlsplit(value)
        if u.scheme != 'https' or u.port or u.username or u.password:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, 'Use a valid HTTPS video link.')
    host = (u.hostname or '').lower()
    def domain(d):
        return host == d or host.endswith('.' + d)
    path = u.path
    if domain('netshort.com') and re.fullmatch(r'/(?:[a-z]{2}/)?episode/[a-z0-9-]+/?', path):
        return 'NetShort'
    if domain('tiktok.com'):
        if path.startswith('/minis/'):
            raise HTTPException(422, 'TikTok Minis is not supported. Use a standard public video link.')
        if host in ('vm.tiktok.com', 'vt.tiktok.com') or re.match(r'/@[^/]+/video/\d+', path) or path.startswith('/t/'):
            return 'TikTok'
    if domain('instagram.com') and re.match(r'/(reel|reels|p|tv)/[A-Za-z0-9_-]+', path):
        return 'Instagram'
    if (domain('x.com') or domain('twitter.com')) and re.match(r'/(?:[^/]+/status|i/status|i/web/status)/\d+', path):
        return 'X'
    if domain('facebook.com') and (re.search(r'/(?:reel|videos|share)/', path) or path.rstrip('/') == '/watch' or path.endswith('/video.php')):
        return 'Facebook'
    if host == 'fb.watch' and len(path) > 1:
        return 'Facebook'
    if domain('snapchat.com') and re.match(r'/spotlight/[\w-]+', path):
        return 'Snapchat'
    raise HTTPException(422, 'Use a direct public Facebook, Instagram, TikTok, X, Snapchat Spotlight or NetShort episode link.')


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPException(422, 'The source redirected this request. Use its direct episode link.')


def request(url):
    return urllib.request.Request(url, headers={'User-Agent': UA, 'Referer': 'https://netshort.com/', 'Origin': 'https://netshort.com'})


def netshort(value):
    opener = urllib.request.build_opener(SafeRedirect())
    with opener.open(request(value), timeout=20) as r:
        raw = r.read(3_000_001)
    if len(raw) > 3_000_000:
        raise HTTPException(422, 'Page too large.')
    source = raw.decode('utf-8')
    chunks = []
    for match in re.finditer(r'self\.__next_f\.push\((\[.*?\])\)</script>', source, re.S):
        try:
            entry = json.loads(match.group(1))
            if isinstance(entry[1], str):
                chunks.append(entry[1])
        except (ValueError, IndexError, TypeError):
            pass
    data = ''.join(chunks)
    marker = '"initialCurrentEpisodeInfo":'
    start = data.find(marker)
    if start < 0:
        raise HTTPException(422, 'This NetShort page format is not supported.')
    try:
        episode, _ = json.JSONDecoder().raw_decode(data[data.index('{', start):])
    except (ValueError, TypeError):
        raise HTTPException(422, 'Cannot read episode metadata.')
    if episode.get('isLock') is not False:
        raise HTTPException(422, 'This episode is locked on NetShort.')
    media = episode.get('playVoucher', '')
    u = urlsplit(media)
    if u.scheme != 'https' or not (u.hostname or '').endswith('.netshort.com') or u.port or u.username or u.password:
        raise HTTPException(422, 'No supported media host is available.')
    title = re.search(r'<title>(.*?)</title>', source, re.S)
    title = html.unescape(re.split(r' Full Episodes| EP \d+', title.group(1))[0]) if title else 'NetShort episode'
    return {'title': title, 'episode': episode.get('episodeNo'), 'duration': float(episode.get('duration') or 0), 'media': media}


def ytdlp(value, directory=None):
    # No shell, configuration, account cookies, plugins, playlists or generic extractor.
    args = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-plugin-dirs',
            '--use-extractors', EXTRACTORS, '--compat-options', 'no-certifi', '--no-playlist', '--no-warnings', '--no-progress',
            '--socket-timeout', '15', '--retries', '1', '--fragment-retries', '1',
            '--max-filesize', '300M']
    if directory:
        args += ['--format', 'bv*[ext=mp4][height<=1080]+ba[ext=m4a]/b[ext=mp4]/b',
                 '--merge-output-format', 'mp4', '--remux-video', 'mp4',
                 '--output', str(directory / 'video.%(ext)s')]
    else:
        args += ['--skip-download', '--dump-single-json']
    args += ['--', value]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=240 if directory else 90)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, 'The source took too long to respond.')
    if p.returncode:
        raise HTTPException(422, 'The platform could not provide this public video. It may require login, be unavailable, or reject server access.')
    if directory:
        file = directory / 'video.mp4'
        if not file.is_file() or file.stat().st_size < 1:
            raise HTTPException(422, 'No compatible MP4 was produced.')
        if file.stat().st_size > LIMIT:
            raise HTTPException(413, 'This video exceeds the 300 MB limit.')
        return file
    try:
        info = json.loads(p.stdout)
    except ValueError:
        raise HTTPException(502, 'Invalid extraction response.')
    if info.get('_type') in ('playlist', 'multi_video') or info.get('is_live') or info.get('has_drm'):
        raise HTTPException(422, 'Use a single, non-live public video without DRM.')
    if float(info.get('duration') or 0) > 1800:
        raise HTTPException(422, 'This release supports videos up to 30 minutes.')
    return info


@app.exception_handler(HTTPException)
async def public_error(req, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse({'error': exc.detail}, status_code=exc.status_code)


@app.get('/health')
def health():
    from yt_dlp.version import __version__
    return {'status': 'ok', 'version': '2.0.0', 'extractor_version': __version__,
            'platforms': ['Facebook', 'Instagram', 'TikTok', 'X', 'Snapchat Spotlight', 'NetShort']}


@app.post('/api/analyze')
def analyze(link: Link):
    platform = classify(link.url)
    if not SLOTS.acquire(blocking=False):
        raise HTTPException(429, 'The server is busy. Try again shortly.')
    try:
        try:
            info = netshort(link.url) if platform == 'NetShort' else ytdlp(link.url)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(502, 'The source could not be reached.')
        return {'title': info.get('title') or 'Video', 'url': link.url, 'platform': platform,
                'episode': info.get('episode'), 'duration': info.get('duration'), 'format': 'mp4'}
    finally:
        SLOTS.release()


@app.get('/api/download')
def download(url: str):
    if len(url) > 2000:
        raise HTTPException(422, 'Link too long.')
    platform = classify(url)
    if not SLOTS.acquire(blocking=False):
        raise HTTPException(429, 'The server is busy. Try again shortly.')
    directory = Path(tempfile.mkdtemp(prefix='videolinksave-'))
    try:
        if platform == 'NetShort':
            info = netshort(url)
            file = directory / 'video.mp4'
            opener = urllib.request.build_opener(SafeRedirect())
            with opener.open(request(info['media']), timeout=30) as r, file.open('wb') as f:
                if not r.headers.get('Content-Type', '').lower().startswith('video/mp4'):
                    raise HTTPException(422, 'No MP4 stream was provided.')
                total = 0
                while chunk := r.read(1024 * 1024):
                    total += len(chunk)
                    if total > LIMIT:
                        raise HTTPException(413, 'Video exceeds the 300 MB limit.')
                    f.write(chunk)
        else:
            # Inspect first to reject playlists/live/DRM before starting a download.
            ytdlp(url)
            file = ytdlp(url, directory)
        return FileResponse(file, media_type='video/mp4', filename=f'videolinksave-{platform.lower()}.mp4',
                            background=BackgroundTask(shutil.rmtree, directory, ignore_errors=True),
                            headers={'Cache-Control': 'no-store'})
    except HTTPException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(502, 'The platform refused the download or the connection failed.')
    finally:
        SLOTS.release()
