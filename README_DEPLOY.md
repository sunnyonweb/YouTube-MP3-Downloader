Simple Docker + Render deployment steps

1) Local build & run (quick test)

```bash
# build image
docker build -t yt-mp3-downloader:latest .

# run (maps container port 5000 -> host 5000)
docker run -d --name yt-mp3 -p 5000:5000 -e FFMPEG_LOCATION=/usr/bin \
  -v yt-mp3-downloads:/app/backend/downloads --restart unless-stopped yt-mp3-downloader:latest

# open http://127.0.0.1:5000/ in your browser
```

2) Deploy to Render (recommended simple container host)

- Create an account at https://render.com and 'New > Web Service'.
- Choose 'Docker' and connect your GitHub repo (or push image to a registry).
- If using repository, Render will build your Dockerfile automatically.
- In Render settings, add an Environment Variable `FFMPEG_LOCATION` = `/usr/bin` (optional, defaults).
- Add a Persistent Disk (if you want to keep downloaded files) and mount to `/app/backend/downloads`.
- Under 'Settings > Custom Domains' add your domain `ck444-game.xyz` and follow Render's DNS instructions.

DNS note for `ck444-game.xyz`:
- Follow the provider-specific instructions Render shows when you add the custom domain. Usually it will ask you to create either an A record or a CNAME pointing to Render's target for your service.
- If you control the DNS at your registrar, paste the records Render gives you there.

3) Quick Fly.io notes (alternative):
- Install `flyctl`, run `fly launch` from project root and pick Docker deploy.
- Add volumes with `fly volumes create` if you need persistent downloads.
- Add your custom domain in Fly dashboard and follow its DNS instructions.

Important production considerations:
- Use HTTPS (Render/Fly handle TLS for you once domain DNS is configured).
- Monitor disk usage and add cleanup or offload to S3 if needed.
- Avoid running long CPU-heavy conversions on very small plans — pick a plan with at least 2 CPUs.
