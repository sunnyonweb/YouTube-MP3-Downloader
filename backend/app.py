from flask import Flask, request, send_file, jsonify, send_from_directory
from flask_cors import CORS
import yt_dlp
import os
import uuid
import threading
import re
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


def run_download_job(job_id, url):
    unique_id = job_id
    output_template = os.path.join(DOWNLOAD_FOLDER, f'{unique_id}.%(ext)s')
    format_selector = 'bestaudio[acodec*=mp3]/bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio[acodec!=none]/bestaudio/best'

    try:
        probe_opts = {
            'format': format_selector,
            'ffmpeg_location': FFMPEG_LOCATION,
            'noplaylist': True,
            'quiet': True,
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
            'noplaylist': True,
            'concurrent_fragment_downloads': 4,
            'progress_hooks': [make_progress_hook(job_id)],
            'quiet': True,
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
    except Exception as e:
        download_jobs[job_id].update({
            'status': 'error',
            'error': f'Download failed: {e}',
        })

@app.route('/download', methods=['POST'])
def download_audio():
    data = request.get_json()
    url = data.get('url', '').strip() if data else ''

    validation_error = validate_youtube_url(url)
    if validation_error:
        return jsonify({'error': validation_error}), 400

    job_id = str(uuid.uuid4())
    download_jobs[job_id] = {
        'status': 'queued',
        'progress': 0,
        'message': 'Queued for download',
    }

    worker = threading.Thread(target=run_download_job, args=(job_id, url), daemon=True)
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