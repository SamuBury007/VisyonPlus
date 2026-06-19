#!/usr/bin/env python3
"""
VixSrc M3U8 Extractor v6 - Relay obbligatorio (token legato all'IP di Render)
"""

import sys
import os
import asyncio
import requests
from urllib.parse import urljoin, quote
from flask import Flask, request, jsonify, render_template_string, Response, stream_with_context

app = Flask(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


# ============================================================
# Playwright - estrazione playlist URL (nessun proxy)
# Il browser gira su Render → il token è legato all'IP di Render
# ============================================================

async def extract_playlist_url(movie_url):
    playlist_urls = []

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720}
        )
        page = await context.new_page()

        async def handle_request(req):
            url = req.url
            if "/playlist/" in url and "vixsrc.to" in url:
                if url not in playlist_urls:
                    playlist_urls.append(url)
                    print(f"[+] PLAYLIST: {url}")
            if "playlist" in url and "m3u8" in url:
                if url not in playlist_urls:
                    playlist_urls.append(url)
                    print(f"[+] M3U8: {url}")

        async def handle_response(resp):
            url = resp.url
            if "/playlist/" in url and "vixsrc.to" in url:
                if url not in playlist_urls:
                    playlist_urls.append(url)
                    print(f"[+] PLAYLIST (resp): {url}")

        page.on("request", handle_request)
        page.on("response", handle_response)

        print(f"[*] Caricamento: {movie_url}")
        try:
            await page.goto(movie_url, wait_until="networkidle", timeout=30000)
            for i in range(15):
                await asyncio.sleep(1)
                if playlist_urls:
                    print(f"   [+] Trovati {len(playlist_urls)} link")
        except Exception as e:
            print(f"[-] Timeout: {e}")
            await asyncio.sleep(5)

        try:
            js_result = await page.evaluate("""
                () => {
                    const results = [];
                    document.querySelectorAll('script').forEach(s => {
                        const text = s.textContent || '';
                        const m1 = text.match(/https?:\\/\\/[^'"\\s]*\\/playlist\\/[^'"\\s]*/g);
                        if (m1) results.push(...m1);
                        const m2 = text.match(/vixsrc\\.to\\/playlist\\/[^'"\\s,&]*/g);
                        if (m2) results.push(...m2.map(u => 'https://' + u));
                    });
                    document.querySelectorAll('*').forEach(el => {
                        if (el.src && el.src.includes('/playlist/')) results.push(el.src);
                        if (el.href && el.href.includes('/playlist/')) results.push(el.href);
                    });
                    return [...new Set(results)];
                }
            """)
            for url in js_result:
                if url.startswith("//"):
                    url = "https:" + url
                elif url.startswith("/"):
                    url = "https://vixsrc.to" + url
                if url not in playlist_urls and "/playlist/" in url:
                    playlist_urls.append(url)
                    print(f"[+] Da JS: {url}")
        except Exception as e:
            print(f"[-] JS extraction: {e}")

        await browser.close()

    return playlist_urls


async def get_best_playlist(movie_url):
    urls = await extract_playlist_url(movie_url)
    if not urls:
        return None

    vixsrc = [u for u in urls if "vixsrc.to/playlist/" in u]
    if vixsrc:
        for q in ["1080p", "1080", "720p", "720"]:
            filtered = [u for u in vixsrc if q in u]
            if filtered:
                return filtered[0]
        return vixsrc[0]

    return urls[0] if urls else None


# ============================================================
# Relay: il browser dell'utente chiama /relay sul server Render,
# che usa il PROPRIO IP (stesso che ha generato il token) per
# scaricare i segmenti da vixsrc e passarli al browser.
# ============================================================

def make_relay_url(original_url):
    host = request.host_url.rstrip('/')
    return f"{host}/relay?url={quote(original_url, safe='')}"


@app.route('/relay')
def relay():
    target_url = request.args.get('url', '')
    if not target_url:
        return "Missing url", 400

    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://vixsrc.to/",
        "Origin": "https://vixsrc.to",
    }

    try:
        resp = requests.get(target_url, headers=headers, timeout=30, stream=True)
    except Exception as e:
        return str(e), 502

    content_type = resp.headers.get('Content-Type', 'application/octet-stream')

    is_m3u8 = (
        "mpegurl" in content_type.lower()
        or target_url.split('?')[0].endswith('.m3u8')
        or "/playlist/" in target_url
    )

    if is_m3u8:
        raw = resp.text
        base_url = target_url.rsplit('/', 1)[0] + '/'
        lines = []
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                if stripped.startswith('http'):
                    abs_url = stripped
                else:
                    abs_url = urljoin(base_url, stripped)
                lines.append(make_relay_url(abs_url))
            else:
                lines.append(line)
        rewritten = '\n'.join(lines)
        return Response(
            rewritten,
            content_type='application/vnd.apple.mpegurl',
            headers={
                'Access-Control-Allow-Origin': '*',
                'Cache-Control': 'no-cache',
            }
        )

    # Segmenti .ts e altri binari — stream diretto
    def generate():
        for chunk in resp.iter_content(chunk_size=8192):
            yield chunk

    return Response(
        stream_with_context(generate()),
        content_type=content_type,
        headers={
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'no-cache',
        }
    )


# ============================================================
# API extract
# ============================================================

@app.route('/extract', methods=['POST'])
def api_extract():
    data = request.get_json()
    movie_url = data.get('url', '')

    if not movie_url:
        return jsonify({'success': False, 'error': 'URL richiesto'})

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        playlist_url = loop.run_until_complete(get_best_playlist(movie_url))
        loop.close()

        if playlist_url:
            # Wrap nel relay: il download avviene dall'IP di Render (stesso che ha il token)
            relay_url = request.host_url.rstrip('/') + '/relay?url=' + quote(playlist_url, safe='')
            return jsonify({
                'success': True,
                'relay_url': relay_url,        # da passare a HLS.js nel browser
                'original_url': playlist_url,  # URL diretto (solo per debug / VLC stesso server)
            })
        else:
            return jsonify({'success': False, 'error': 'Nessun link playlist trovato.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ============================================================
# HTML UI con HLS.js player
# ============================================================

HTML_TEMPLATE = '''<!DOCTYPE html>
<html>
<head>
    <title>VixSrc Extractor</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.7/dist/hls.min.js"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               background: #0f0f0f; color: #e0e0e0; line-height: 1.6; }
        .container { max-width: 900px; margin: 40px auto; padding: 0 20px; }
        h1 { color: #00d4aa; font-size: 1.8em; margin-bottom: 4px; }
        .subtitle { color: #888; margin-bottom: 24px; }
        .card { background: #1a1a2e; border-radius: 12px; padding: 28px; border: 1px solid #2a2a4a; margin-bottom: 20px; }
        input[type="text"] { width: 100%; padding: 12px 16px; background: #0f0f1a;
                             border: 1px solid #333; border-radius: 8px; color: #fff;
                             font-size: 1em; margin-bottom: 14px; }
        input[type="text"]:focus { outline: none; border-color: #00d4aa; }
        .btn { background: #00d4aa; color: #000; border: none; padding: 12px 24px;
               border-radius: 8px; font-size: 1em; font-weight: 600; cursor: pointer; }
        .btn:hover { background: #00f0c0; }
        .btn-copy { background: #333; color: #fff; border: none; padding: 6px 14px;
                    border-radius: 4px; cursor: pointer; font-size: 0.82em; margin-left: 8px; }
        .btn-copy:hover { background: #444; }
        .result { margin-top: 16px; padding: 14px; background: #0f0f1a; border-radius: 8px;
                  border: 1px solid #2a2a4a; display: none; word-break: break-all; }
        .result.success { border-color: #00d4aa; display: block; }
        .result.error { border-color: #ff4444; display: block; }
        .result code { color: #00d4aa; font-size: 0.82em; }
        .loader { display: none; margin: 14px 0; }
        .loader.active { display: block; }
        .spinner { display: inline-block; width: 18px; height: 18px; border: 3px solid #333;
                    border-top: 3px solid #00d4aa; border-radius: 50%;
                    animation: spin 0.8s linear infinite; margin-right: 8px; vertical-align: middle; }
        @keyframes spin { to { transform: rotate(360deg); } }
        #player-section { display: none; }
        video { width: 100%; border-radius: 8px; background: #000; max-height: 500px; }
        .url-row { margin-top: 10px; font-size: 0.82em; color: #888; }
    </style>
</head>
<body>
<div class="container">
    <h1>VixSrc Extractor</h1>
    <p class="subtitle">Estrai e riproduci direttamente nel browser</p>

    <div class="card">
        <label for="url-input">URL film su vixsrc.to</label>
        <input type="text" id="url-input" placeholder="https://vixsrc.to/movie/786892/" />
        <button class="btn" onclick="extract()">⬇ Estrai e Riproduci</button>

        <div class="loader" id="loader">
            <span class="spinner"></span> Estrazione in corso (15-20 sec)...
        </div>
        <div class="result" id="result"></div>
    </div>

    <div class="card" id="player-section">
        <h2 style="color:#00d4aa; margin-bottom:14px;">▶ Player</h2>
        <video id="video" controls></video>
        <div class="url-row">
            <strong>Relay URL:</strong> <code id="relay-url-display"></code>
            <button class="btn-copy" onclick="copyRelay()">📋 Copia relay</button>
        </div>
        <div class="url-row" style="margin-top:6px;">
            <strong>URL diretto vixsrc:</strong> <code id="orig-url-display"></code>
            <button class="btn-copy" onclick="copyOrig()">📋 Copia diretto</button>
        </div>
        <div class="url-row" style="margin-top:6px; color:#f90;">
            ⚠ L'URL diretto funziona solo se aperto dallo stesso IP del server (es. VLC sul server stesso). Per il browser usa il Relay URL.
        </div>
    </div>
</div>

<script>
    let relayUrl = '';
    let origUrl = '';
    let hlsInstance = null;

    async function extract() {
        const url = document.getElementById('url-input').value.trim();
        if (!url) { showError('Inserisci un URL'); return; }

        document.getElementById('loader').classList.add('active');
        document.getElementById('result').className = 'result';
        document.getElementById('player-section').style.display = 'none';
        if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }

        try {
            const resp = await fetch('/extract', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url })
            });
            const data = await resp.json();
            document.getElementById('loader').classList.remove('active');

            if (data.success) {
                relayUrl = data.relay_url;
                origUrl = data.original_url;

                document.getElementById('result').className = 'result success';
                document.getElementById('result').innerHTML = '✅ Playlist trovata! Avvio player...';

                document.getElementById('relay-url-display').textContent = relayUrl;
                document.getElementById('orig-url-display').textContent = origUrl;
                document.getElementById('player-section').style.display = 'block';

                playHLS(relayUrl);
            } else {
                showError(data.error);
            }
        } catch (err) {
            document.getElementById('loader').classList.remove('active');
            showError(err.message);
        }
    }

    function playHLS(src) {
        const video = document.getElementById('video');
        if (Hls.isSupported()) {
            hlsInstance = new Hls({ enableWorker: true });
            hlsInstance.loadSource(src);
            hlsInstance.attachMedia(video);
            hlsInstance.on(Hls.Events.MANIFEST_PARSED, () => video.play());
            hlsInstance.on(Hls.Events.ERROR, (event, data) => {
                if (data.fatal) showError('HLS error: ' + data.details);
            });
        } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
            video.src = src;
            video.play();
        } else {
            showError('HLS non supportato in questo browser');
        }
    }

    function showError(msg) {
        document.getElementById('result').className = 'result error';
        document.getElementById('result').innerHTML = '❌ ' + msg;
    }

    function copyRelay() { navigator.clipboard.writeText(relayUrl).then(() => flash(event.target)); }
    function copyOrig()  { navigator.clipboard.writeText(origUrl).then(() => flash(event.target)); }
    function flash(btn) {
        const orig = btn.textContent;
        btn.textContent = '✅ Copiato!';
        setTimeout(() => btn.textContent = orig, 2000);
    }
</script>
</body>
</html>'''


# ============================================================
# Main
# ============================================================

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


if __name__ == '__main__':
    if len(sys.argv) > 1:
        async def main():
            movie_url = sys.argv[1]
            print(f"[*] Estrazione da: {movie_url}")
            url = await get_best_playlist(movie_url)
            if url:
                print(f"\n[+] Link playlist:\n    {url}")
            else:
                print("\n[-] Nessuna playlist trovata")
        asyncio.run(main())
    else:
        port = int(os.environ.get("PORT", 8080))
        print(f"[*] Avvio su http://0.0.0.0:{port}")
        app.run(host='0.0.0.0', port=port, debug=False)
