#!/usr/bin/env python3
"""
VixSrc M3U8 Extractor v4 - Cattura il link playlist di vixsrc.to
"""

import re
import sys
import json
import asyncio
import requests
from urllib.parse import urlparse, parse_qs, urlencode
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


async def extract_playlist_url(movie_url):
    """
    Usa Playwright per catturare SPECIFICAMENTE le richieste
    a vixsrc.to/playlist/... che sono i link M3U8 funzionanti
    """
    playlist_urls = []
    
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        # Modificato per supportare l'esecuzione sicura in Docker (--no-sandbox)
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720}
        )
        page = await context.new_page()
        
        # Intercetta TUTTE le richieste
        async def handle_request(request):
            url = request.url
            # Cerchiamo SOLO i link /playlist/ su vixsrc.to
            if "/playlist/" in url and "vixsrc.to" in url:
                if url not in playlist_urls:
                    playlist_urls.append(url)
                    print(f"[+] PLAYLIST: {url}")
            
            # Anche se ha /playlist/ in altri formati
            if "playlist" in url and "m3u8" in url:
                if url not in playlist_urls:
                    playlist_urls.append(url)
                    print(f"[+] M3U8: {url}")
        
        async def handle_response(response):
            url = response.url
            # Cattura anche i redirect che portano a playlist
            if "/playlist/" in url and "vixsrc.to" in url:
                if url not in playlist_urls:
                    playlist_urls.append(url)
                    print(f"[+] PLAYLIST (resp): {url}")
        
        page.on("request", handle_request)
        page.on("response", handle_response)
        
        print(f"[*] Caricamento: {movie_url}")
        print("[*] In attesa di richieste a /playlist/...")
        
        try:
            await page.goto(movie_url, wait_until="networkidle", timeout=30000)
            # Aspetta più a lungo per catturare tutto
            for i in range(15):
                await asyncio.sleep(1)
                if playlist_urls:
                    print(f"   [+] Già trovati {len(playlist_urls)} link playlist")
        except Exception as e:
            print(f"[-] Timeout, ma continuo...")
            await asyncio.sleep(5)
        
        # Prova anche con evaluate per estrarre direttamente dal JS
        try:
            print("[*] Provo estrazione dal JavaScript...")
            js_result = await page.evaluate("""
                () => {
                    const results = [];
                    // Cerca in tutti gli script tag
                    document.querySelectorAll('script').forEach(s => {
                        const text = s.textContent || '';
                        // Cerca URL con /playlist/
                        const matches = text.match(/https?:\\/\\/[^'"\\s]*\\/playlist\\/[^'"\\s]*/g);
                        if (matches) results.push(...matches);
                        // Cerca URL vixsrc.to/playlist
                        const matches2 = text.match(/vixsrc\\.to\\/playlist\\/[^'"\\s,&]*/g);
                        if (matches2) results.push(...matches2.map(u => 'https://' + u));
                    });
                    // Cerca nel DOM
                    const all = document.querySelectorAll('*');
                    all.forEach(el => {
                        if (el.src && el.src.includes('/playlist/')) results.push(el.src);
                        if (el.href && el.href.includes('/playlist/')) results.push(el.href);
                        if (el.data && typeof el.data === 'string' && el.data.includes('/playlist/')) results.push(el.data);
                    });
                    return [...new Set(results)];
                }
            """)
            for url in js_result:
                # Normalizza: se inizia con // o è relativo
                if url.startswith("//"):
                    url = "https:" + url
                elif url.startswith("/"):
                    url = "https://vixsrc.to" + url
                if url not in playlist_urls and ("/playlist/" in url):
                    playlist_urls.append(url)
                    print(f"[+] Da JS: {url}")
        except Exception as e:
            print(f"[-] JS extraction: {e}")
        
        await browser.close()
    
    return playlist_urls


async def get_best_playlist(movie_url):
    """Trova la playlist migliore"""
    
    urls = await extract_playlist_url(movie_url)
    
    if not urls:
        return None
    
    print(f"\n[*] Trovati {len(urls)} link playlist:")
    for u in urls:
        print(f"   - {u}")
    
    # Filtra: vogliamo quelli su vixsrc.to/playlist/
    vixsrc_playlists = [u for u in urls if "vixsrc.to/playlist/" in u]
    
    if vixsrc_playlists:
        # Preferisci 1080p
        _1080p = [u for u in vixsrc_playlists if "1080p" in u or "1080" in u]
        if _1080p:
            return _1080p[0]
        # Poi 720p
        _720p = [u for u in vixsrc_playlists if "720p" in u or "720" in u]
        if _720p:
            return _720p[0]
        # Altrimenti il primo
        return vixsrc_playlists[0]
    
    # Fallback: qualsiasi playlist
    if urls:
        return urls[0]
    
    return None


# ============================================================
# Flask Routes
# ============================================================

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


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
            return jsonify({
                'success': True,
                'url': playlist_url,
            })
        else:
            return jsonify({'success': False, 'error': 'Nessun link playlist trovato. Il player potrebbe usare un dominio diverso.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ============================================================
# HTML UI
# ============================================================

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>VixSrc Playlist Extractor</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
               background: #0f0f0f; color: #e0e0e0; line-height: 1.6; }
        .container { max-width: 800px; margin: 50px auto; padding: 0 20px; }
        h1 { color: #00d4aa; font-size: 1.8em; }
        .subtitle { color: #888; margin-bottom: 30px; }
        .card { background: #1a1a2e; border-radius: 12px; padding: 30px; border: 1px solid #2a2a4a; }
        input[type="text"] { width: 100%; padding: 12px 16px; background: #0f0f1a;
                             border: 1px solid #333; border-radius: 8px; color: #fff;
                             font-size: 1em; margin-bottom: 15px; }
        input[type="text"]:focus { outline: none; border-color: #00d4aa; }
        button { background: #00d4aa; color: #000; border: none; padding: 12px 24px;
                 border-radius: 8px; font-size: 1em; font-weight: 600; cursor: pointer; }
        button:hover { background: #00f0c0; }
        .result { margin-top: 20px; padding: 15px; background: #0f0f1a; border-radius: 8px;
                  border: 1px solid #2a2a4a; word-break: break-all; display: none; }
        .result.success { border-color: #00d4aa; display: block; }
        .result.error { border-color: #ff4444; display: block; }
        .result code { color: #00d4aa; font-size: 0.85em; }
        .loader { display: none; margin: 15px 0; }
        .loader.active { display: block; }
        .spinner { display: inline-block; width: 18px; height: 18px; border: 3px solid #333;
                    border-top: 3px solid #00d4aa; border-radius: 50%;
                    animation: spin 0.8s linear infinite; margin-right: 8px; vertical-align: middle; }
        @keyframes spin { to { transform: rotate(360deg); } }
        .copy-btn { background: #333; color: #fff; border: none; padding: 6px 14px;
                    border-radius: 4px; cursor: pointer; font-size: 0.85em; margin-left: 8px; }
        .copy-btn:hover { background: #444; }
        .url-display { color: #888; font-size: 0.85em; margin-top: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>VixSrc Playlist Extractor</h1>
        <p class="subtitle">Incolla il link del film, ottieni il link playlist da usare in VLC</p>

        <div class="card">
            <label for="url-input">URL del film su vixsrc.to</label>
            <input type="text" id="url-input" placeholder="https://vixsrc.to/movie/786892/" />

            <button onclick="extract()"> Estrai Link Playlist</button>

            <div class="loader" id="loader">
                <span class="spinner"></span> Estrazione in corso (15-20 secondi)...
            </div>

            <div class="result" id="result"></div>
        </div>
    </div>

    <script>
        async function extract() {
            const url = document.getElementById('url-input').value.trim();
            if (!url) {
                document.getElementById('result').className = 'result error';
                document.getElementById('result').innerHTML = 'Inserisci un URL';
                return;
            }

            document.getElementById('loader').classList.add('active');
            document.getElementById('result').className = 'result';

            try {
                const resp = await fetch('/extract', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: url })
                });

                const data = await resp.json();
                document.getElementById('loader').classList.remove('active');

                if (data.success) {
                    document.getElementById('result').className = 'result success';
                    document.getElementById('result').innerHTML =
                        '<strong>Playlist trovata!</strong><br><br>' +
                        '<span style="color:#888;">Copia e incolla in VLC (Ctrl+N):</span><br><br>' +
                        '<code id="playlisturl">' + data.url + '</code>' +
                        '<button class="copy-btn" onclick="copyUrl()"> Copia</button><br><br>' +
                        '<span class="url-display">In VLC: Ctrl+N → incolla → Play</span>';
                } else {
                    document.getElementById('result').className = 'result error';
                    document.getElementById('result').innerHTML = 'Errore: ' + data.error;
                }
            } catch (err) {
                document.getElementById('loader').classList.remove('active');
                document.getElementById('result').className = 'result error';
                document.getElementById('result').innerHTML = 'Errore: ' + err.message;
            }
        }

        function copyUrl() {
            const url = document.getElementById('playlisturl').textContent;
            navigator.clipboard.writeText(url).then(() => {
                const btn = document.querySelector('.copy-btn');
                btn.textContent = ' Copiato!';
                setTimeout(() => btn.textContent = ' Copia', 2000);
            });
        }
    </script>
</body>
</html>
'''


# ============================================================
# Main
# ============================================================

if __name__ == '__main__':
    if len(sys.argv) > 1:
        async def main():
            movie_url = sys.argv[1]
            print(f"[*] Estrazione da: {movie_url}")
            url = await get_best_playlist(movie_url)
            if url:
                print(f"\n[+] Link playlist:")
                print(f"    {url}")
                print(f"\n[+] Aprilo in VLC: Ctrl+N → incolla → Play")
            else:
                print("\n[-] Nessuna playlist trovata")
        
        asyncio.run(main())
    else:
        app.run(host='0.0.0.0', port=8080, debug=True)
