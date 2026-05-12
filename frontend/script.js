async function downloadAudio() {
  const url = document.getElementById('youtubeUrl').value.trim();
  const status = document.getElementById('status');
  const apiBase = (window.API_BASE && window.API_BASE.replace(/\/$/, '')) || 'http://127.0.0.1:5000';

  if (!url) {
    status.innerText = 'Please enter a YouTube URL';
    return;
  }

  if (url.startsWith('file://')) {
    status.innerText = 'Please paste a YouTube link, not a file:// URL';
    return;
  }

  try {
    const parsedUrl = new URL(url);
    const host = parsedUrl.hostname.replace(/^www\./, '');
    const allowedHosts = ['youtube.com', 'youtu.be', 'm.youtube.com', 'music.youtube.com'];

    if (!allowedHosts.includes(host) && !allowedHosts.some((allowedHost) => host.endsWith('.' + allowedHost))) {
      status.innerText = 'This downloader only supports YouTube URLs';
      return;
    }
  } catch (parseError) {
    status.innerText = 'Please paste a valid YouTube URL';
    return;
  }

  status.innerText = 'Starting download...';
  let pollTimer = null;

  try {
    const response = await fetch(`${apiBase}/download`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ url })
    });

    if (!response.ok) {
      let errorMessage = 'Download failed';
      try {
        const errorData = await response.json();
        if (errorData && errorData.error) {
          errorMessage = errorData.error;
        }
      } catch (parseError) {
        // Keep the generic message when the backend does not return JSON.
      }
      throw new Error(errorMessage);
    }

    const responseData = await response.json();
    const jobId = responseData.job_id;

    const pollStatus = async () => {
      const statusResponse = await fetch(`${apiBase}/status/${jobId}`);
      const job = await statusResponse.json();

      if (job.status === 'error') {
        status.innerText = 'Error: ' + job.error;
        clearInterval(pollTimer);
        return;
      }

      if (job.status === 'processing') {
        status.innerText = job.message || 'Converting to MP3...';
        return;
      }

      if (job.status === 'done') {
        clearInterval(pollTimer);
        status.innerText = 'Download Complete!';

        const a = document.createElement('a');
        a.href = `${apiBase}/file/${jobId}`;
        a.download = job.download_name || 'audio.mp3';
        document.body.appendChild(a);
        a.click();
        a.remove();
        return;
      }

      if (typeof job.progress === 'number') {
        status.innerText = `Downloading... ${job.progress}%`;
      } else {
        status.innerText = job.message || 'Downloading...';
      }
    };

    pollTimer = setInterval(() => {
      pollStatus().catch((pollError) => {
        status.innerText = 'Error: ' + pollError.message;
        clearInterval(pollTimer);
      });
    }, 1500);

    await pollStatus();

  } catch (error) {
    status.innerText = 'Error: ' + error.message;
  }
}

async function uploadCookies() {
  const fileInput = document.getElementById('cookiesFile');
  const statusDiv = document.getElementById('cookiesStatus');
  const apiBase = (window.API_BASE && window.API_BASE.replace(/\/$/, '')) || 'http://127.0.0.1:5000';

  if (!fileInput.files || fileInput.files.length === 0) {
    statusDiv.innerText = '❌ Please select a cookies file';
    return;
  }

  const file = fileInput.files[0];
  const formData = new FormData();
  formData.append('cookies', file);

  try {
    statusDiv.innerText = '⏳ Uploading cookies...';

    const response = await fetch(`${apiBase}/upload-cookies`, {
      method: 'POST',
      body: formData
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.error || 'Upload failed');
    }

    const result = await response.json();
    statusDiv.innerText = '✅ ' + result.message;
    fileInput.value = '';

    // Refresh cookies status after 1 second
    setTimeout(checkCookiesStatus, 1000);
  } catch (error) {
    statusDiv.innerText = '❌ Error: ' + error.message;
  }
}

async function checkCookiesStatus() {
  const statusDiv = document.getElementById('cookiesStatus');
  const apiBase = (window.API_BASE && window.API_BASE.replace(/\/$/, '')) || 'http://127.0.0.1:5000';

  try {
    const response = await fetch(`${apiBase}/cookies-status`);
    const data = await response.json();

    if (data.has_cookies) {
      statusDiv.innerText = '✅ Cookies loaded - YouTube authentication enabled';
    } else {
      statusDiv.innerText = '⚠️ No cookies uploaded yet';
    }
  } catch (error) {
    // Silently fail for cookies status check
    statusDiv.innerText = '⚠️ Could not check cookies status';
  }
}

// Check cookies status on page load
window.addEventListener('load', () => {
  checkCookiesStatus();
});