from flask import Flask, request, send_file, jsonify, send_from_directory
from flask_cors import CORS
import yt_dlp
import os
import uuid
import threading
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

app = Flask(__name__)
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
FRONTEND_DIR = os.path.join(PROJECT_ROOT, 'frontend')
DOWNLOAD_FOLDER = os.path.join(BASE_DIR, 'downloads')
# Allow overriding ffmpeg location via environment (useful in Docker).
FFMPEG_LOCATION = os.environ.get('FFMPEG_LOCATION') or os.path.join(PROJECT_ROOT, 'ffmpeg-8.1.1-essentials_build', 'bin')
AUDIO_QUALITY = '9'

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

download_jobs = {}


@app.route('/')
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/<path:filename>')
def frontend_assets(filename):
    return send_from_directory(FRONTEND_DIR, filename)


def validate_youtube_url(raw_url):
    if not raw_url:
        return 'No URL provided'

    parsed_url = urlparse(raw_url.strip())

    if parsed_url.scheme == 'file':
        return 'file:// URLs are not supported. Paste a YouTube link instead.'

    if parsed_url.scheme not in ('http', 'https'):
        return 'Please paste a valid http(s) YouTube URL.'

    host = parsed_url.netloc.lower()
    if host.startswith('www.'):
        host = host[4:]

    allowed_hosts = ('youtube.com', 'youtu.be', 'm.youtube.com', 'music.youtube.com')
    if not any(host == allowed_host or host.endswith('.' + allowed_host) for allowed_host in allowed_hosts):
        return 'This downloader only supports YouTube URLs.'

    return None


def build_download_name(title):
    # Keep the original title as much as possible; only remove Windows-illegal chars.
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1F]', '', (title or '').strip())
    safe_title = safe_title.rstrip(' .')
    if not safe_title:
        safe_title = 'audio'
    return f'{safe_title}.mp3'


def make_progress_hook(job_id):
    def progress_hook(data):
        job = download_jobs.get(job_id)
        if not job:
            return

        status = data.get('status')
        if status == 'downloading':
            total_bytes = data.get('total_bytes') or data.get('total_bytes_estimate')
            downloaded_bytes = data.get('downloaded_bytes', 0)
            progress = None
            if total_bytes:
                progress = round(downloaded_bytes * 100 / total_bytes, 1)

            job.update({
                'status': 'downloading',
                'progress': progress,
                'downloaded_bytes': downloaded_bytes,
                'total_bytes': total_bytes,
                'speed': data.get('speed'),
                'eta': data.get('eta'),
            })
        elif status == 'finished':
            if job.get('needs_conversion', True):
                job.update({
                    'status': 'processing',
                    'progress': 95,
                    'message': 'Download finished, converting to MP3...',
                })
            else:
                job.update({
                    'status': 'processing',
                    'progress': 99,
                    'message': 'Download finished, finalizing file...',
                })

    return progress_hook


def run_download_job(job_id, url, cookies_file=None):
    import time
    
    unique_id = job_id
    output_template = os.path.join(DOWNLOAD_FOLDER, f'{unique_id}.%(ext)s')
    format_selector = 'bestaudio[acodec*=mp3]/bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio[acodec!=none]/bestaudio/best'

    # Common options for both probe and download, with aggressive YouTube bypass
    def get_ydl_opts(is_download=False):
        opts = {
            'socket_timeout': 60,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            },
            'geo_bypass': True,
            'geo_bypass_country': 'US',
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'mweb', 'web'],
                    'player_skip': ['js', 'webpage'],
                }
            },
            'noplaylist': True,
            'quiet': True,
            'no_warnings': False,
            'retries': 5,
        }
        if cookies_file and os.path.exists(cookies_file) and os.path.getsize(cookies_file) > 0:
            opts['cookiefile'] = cookies_file
        return opts

    max_retries = 3
    last_error = None
    
    try:
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    wait_time = (2 ** attempt)  # exponential backoff: 2, 4, 8 seconds
                    download_jobs[job_id].update({
                        'status': 'downloading',
                        'progress': 0,
                        'message': f'Retrying after {wait_time}s... (attempt {attempt + 1}/{max_retries})',
                    })
                    time.sleep(wait_time)

                probe_opts = {
                    'format': format_selector,
                    'ffmpeg_location': FFMPEG_LOCATION,
                    **get_ydl_opts(is_download=False),
                }

                with yt_dlp.YoutubeDL(probe_opts) as probe:
                    probe_info = probe.extract_info(url, download=False)

                source_ext = (probe_info.get('ext') or '').lower()
                source_acodec = (probe_info.get('acodec') or '').lower()
                needs_conversion = not (source_ext == 'mp3' or 'mp3' in source_acodec)
                download_jobs[job_id]['needs_conversion'] = needs_conversion

                ydl_opts = {
                    'format': format_selector,
                    'outtmpl': output_template,
                    'ffmpeg_location': FFMPEG_LOCATION,
                    'concurrent_fragment_downloads': 4,
                    'progress_hooks': [make_progress_hook(job_id)],
                    **get_ydl_opts(is_download=True),
                }

                if needs_conversion:
                    ydl_opts['postprocessors'] = [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': AUDIO_QUALITY,
                    }]
                    ydl_opts['postprocessor_args'] = ['-threads', '0']

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    title = info.get('title', 'audio')

                mp3_ext = 'mp3' if needs_conversion else (source_ext or 'mp3')
                mp3_file = os.path.join(DOWNLOAD_FOLDER, f'{unique_id}.{mp3_ext}')
                if not os.path.exists(mp3_file):
                    download_jobs[job_id].update({
                        'status': 'error',
                        'error': 'MP3 conversion failed: output file was not created',
                    })
                    return

                download_jobs[job_id].update({
                    'status': 'done',
                    'progress': 100,
                    'title': title,
                    'download_name': build_download_name(title),
                    'file_path': mp3_file,
                })
                return

            except Exception as e:
                last_error = str(e)
                if attempt < max_retries - 1:
                    continue
                download_jobs[job_id].update({
                    'status': 'error',
                    'error': f'Download failed after {max_retries} attempts: {last_error}',
                })
                return
    finally:
        if cookies_file:
            try:
                if os.path.exists(cookies_file):
                    os.remove(cookies_file)
            except OSError:
                pass

@app.route('/download', methods=['POST'])
def download_audio():
    url = ''
    cookies_file_path = None

    if request.content_type and request.content_type.startswith('multipart/form-data'):
        url = (request.form.get('url') or '').strip()
        cookies_upload = request.files.get('cookies')
        if cookies_upload and cookies_upload.filename:
            suffix = Path(cookies_upload.filename).suffix.lower()
            if suffix not in ('.txt', '.json'):
                return jsonify({'error': 'Only .txt or .json cookies files are supported'}), 400

            temp_file = tempfile.NamedTemporaryFile(delete=False, dir=DOWNLOAD_FOLDER, prefix='cookies_', suffix=suffix)
            temp_file.close()
            cookies_upload.save(temp_file.name)
            cookies_file_path = temp_file.name
    else:
        data = request.get_json()
        url = data.get('url', '').strip() if data else ''

    validation_error = validate_youtube_url(url)
    if validation_error:
        if cookies_file_path and os.path.exists(cookies_file_path):
            os.remove(cookies_file_path)
        return jsonify({'error': validation_error}), 400

    job_id = str(uuid.uuid4())
    download_jobs[job_id] = {
        'status': 'queued',
        'progress': 0,
        'message': 'Queued for download',
    }

    worker = threading.Thread(target=run_download_job, args=(job_id, url, cookies_file_path), daemon=True)
    worker.start()

    return jsonify({
        'job_id': job_id,
        'status_url': f'/status/{job_id}',
        'file_url': f'/file/{job_id}',
    }), 202


@app.route('/status/<job_id>', methods=['GET'])
def download_status(job_id):
    job = download_jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Unknown job id'}), 404

    return jsonify(job)


@app.route('/file/<job_id>', methods=['GET'])
def download_file(job_id):
    job = download_jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Unknown job id'}), 404

    if job.get('status') != 'done':
        return jsonify({'error': 'File is not ready yet'}), 409

    file_path = job.get('file_path')
    if not file_path or not os.path.exists(file_path):
        return jsonify({'error': 'Downloaded file not found'}), 404

    return send_file(
        file_path,
        as_attachment=True,
        download_name=job.get('download_name', 'audio.mp3')
    )


if __name__ == '__main__':
    app.run(debug=True)