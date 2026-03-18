#!/usr/bin/env python3
"""
iPod Shuffle Manager - Web-based GUI for managing songs and playlists.
Usage: python ipod_manager.py E:\
"""

import sys
import os
import json
import shutil
import subprocess
import threading
import webbrowser
import io
import re
import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
import cgi

try:
    import mutagen
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4
    from mutagen.id3 import ID3
    MUTAGEN = True
except ImportError:
    MUTAGEN = False

# ─── Logging ───────────────────────────────────────────────────────────────────
class GuiLogger:
    def __init__(self, capacity=200):
        self.log = []
        self.capacity = capacity
        self.lock = threading.Lock()

    def info(self, message):
        with self.lock:
            msg = f"[INFO] {message}"
            self.log.append(msg)
            if len(self.log) > self.capacity:
                self.log.pop(0)
            print(msg)

    def error(self, message):
        with self.lock:
            msg = f"[ERROR] {message}"
            self.log.append(msg)
            if len(self.log) > self.capacity:
                self.log.pop(0)
            print(msg)

    def get_logs(self):
        with self.lock:
            return "\n".join(self.log)

logger = GuiLogger()

# ─── Config ────────────────────────────────────────────────────────────────────
PORT = 7734
AUDIO_EXT = ('.mp3', '.m4a', '.m4b', '.m4p', '.aa', '.wav')
PLAYLIST_EXT = ('.m3u', '.m3u8')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SYNC_SCRIPT = os.path.join(SCRIPT_DIR, 'ipod-shuffle-4g.py')
STAGING_FOLDER = '.staging'  # hidden folder inside iPod_Control/Music; cleared on startup/exit

# ─── Helpers ───────────────────────────────────────────────────────────────────
def format_duration(seconds):
    if not seconds: return "00:00"
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"

def md5_of_file(path, chunk=1 << 20):
    """Return MD5 hex digest of a file's contents."""
    h = hashlib.md5()
    try:
        with open(path, 'rb') as f:
            while True:
                block = f.read(chunk)
                if not block:
                    break
                h.update(block)
    except Exception:
        return None
    return h.hexdigest()

def find_duplicates(ipod_path):
    """Scan all audio files and return a dict: md5 -> list of full paths.
    Only entries with >1 path are duplicates."""
    music_dir = os.path.join(ipod_path, 'iPod_Control', 'Music')
    by_hash = {}
    for dirpath, dirnames, filenames in os.walk(music_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for filename in filenames:
            if filename.startswith('.'):
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext in AUDIO_EXT:
                full = os.path.join(dirpath, filename)
                digest = md5_of_file(full)
                if digest:
                    by_hash.setdefault(digest, []).append(full)
    return {k: v for k, v in by_hash.items() if len(v) > 1}

def cleanup_staging(ipod_path):
    """Remove the staging folder and everything inside it."""
    staging = os.path.join(ipod_path, 'iPod_Control', 'Music', STAGING_FOLDER)
    if os.path.isdir(staging):
        shutil.rmtree(staging, ignore_errors=True)
        logger.info('Staging folder cleared.')

def get_song_info(path):
    """Read ID3/M4A tags and return title, artist, album, duration."""
    title = os.path.splitext(os.path.basename(path))[0]
    artist = ''
    album = ''
    duration_str = '00:00'
    if MUTAGEN:
        try:
            audio = mutagen.File(path, easy=True)
            if audio:
                title = audio.get('title', [title])[0]
                artist = audio.get('artist', [''])[0]
                album = audio.get('album', [''])[0]
                if hasattr(audio.info, 'length'):
                    duration_str = format_duration(audio.info.length)
        except Exception:
            pass
    return title, artist, album, duration_str

def scan_songs(ipod_path):
    """Walk the iPod and return list of song dicts with staged + duplicate flags."""
    staging_dir = os.path.join(ipod_path, 'iPod_Control', 'Music', STAGING_FOLDER)
    dup_map = find_duplicates(ipod_path)  # md5 -> [paths]
    # Build reverse lookup: full_path -> list of other duplicate paths
    path_to_dups = {}
    for paths in dup_map.values():
        for p in paths:
            path_to_dups[p] = [x for x in paths if x != p]

    songs = []
    music_dir = os.path.join(ipod_path, 'iPod_Control', 'Music')
    pos = 1
    for dirpath, dirnames, filenames in os.walk(music_dir):
        dirnames.sort()
        # skip hidden directories (but DO descend into .staging)
        dirnames[:] = [d for d in dirnames if not d.startswith('.') or d == STAGING_FOLDER]
        for filename in sorted(filenames):
            if filename.startswith('.'):
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext in AUDIO_EXT:
                full = os.path.join(dirpath, filename)
                title, artist, album, duration = get_song_info(full)
                rel = os.path.relpath(full, ipod_path).replace(os.sep, '/')
                is_staged = os.path.commonpath([full, staging_dir]) == staging_dir if os.path.isdir(staging_dir) else False
                dup_rels = [os.path.relpath(d, ipod_path).replace(os.sep, '/') for d in path_to_dups.get(full, [])]
                songs.append({
                    'pos': pos,
                    'path': rel,
                    'full': full,
                    'title': title,
                    'artist': artist,
                    'album': album,
                    'duration': duration,
                    'filename': filename,
                    'staged': is_staged,
                    'duplicate_of': dup_rels,
                })
                pos += 1
    return songs

def scan_playlists(ipod_path):
    """Find all .m3u/.m3u8 files under iPod_Control/Music and return list of playlist dicts."""
    playlists = []
    music_dir = os.path.join(ipod_path, 'iPod_Control', 'Music')
    for dirpath, _, filenames in os.walk(music_dir):
        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext in PLAYLIST_EXT:
                full = os.path.join(dirpath, filename)
                name = os.path.splitext(filename)[0]
                tracks = parse_m3u(full, ipod_path)
                playlists.append({'name': name, 'path': full, 'tracks': tracks})
    return playlists

def parse_m3u(path, ipod_path):
    """Parse an m3u/m3u8 file and return list of iPod-relative track paths."""
    tracks = []
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    # Could be relative to playlist file, or to ipod root
                    # Normalise to ipod-relative forward-slash path
                    line = line.replace('\\', '/')
                    # Resolve relative to the playlist file's directory
                    playlist_dir = os.path.dirname(path)
                    candidate = os.path.normpath(os.path.join(playlist_dir, line.replace('/', os.sep)))
                    if os.path.isfile(candidate):
                        rel = os.path.relpath(candidate, ipod_path).replace(os.sep, '/')
                        tracks.append(rel)
                    else:
                        # Maybe relative to ipod root
                        candidate2 = os.path.normpath(os.path.join(ipod_path, line.replace('/', os.sep)))
                        if os.path.isfile(candidate2):
                            rel = os.path.relpath(candidate2, ipod_path).replace(os.sep, '/')
                            tracks.append(rel)
    except Exception as e:
        logger.error(f'Error parsing playlist {path}: {e}')
    return tracks

def save_playlist(ipod_path, name, tracks):
    """Write a .m3u playlist file to iPod_Control/Music/."""
    music_dir = os.path.join(ipod_path, 'iPod_Control', 'Music')
    filename = re.sub(r'[<>:"/\\|?*]', '_', name) + '.m3u'
    filepath = os.path.join(music_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('#EXTM3U\n')
        for track_rel in tracks:
            # Write path relative to playlist file (which is in music_dir)
            full_track = os.path.normpath(os.path.join(ipod_path, track_rel.replace('/', os.sep)))
            rel_to_playlist = os.path.relpath(full_track, music_dir).replace(os.sep, '/')
            f.write(rel_to_playlist + '\n')
    return filepath

def delete_playlist(ipod_path, name):
    """Delete a playlist file."""
    music_dir = os.path.join(ipod_path, 'iPod_Control', 'Music')
    for ext in PLAYLIST_EXT:
        candidate = os.path.join(music_dir, name + ext)
        if os.path.isfile(candidate):
            os.remove(candidate)
            return True
    return False

def windows_tts_wav(text, out_wav):
    """Generate a WAV file using Windows SAPI TTS via PowerShell."""
    ps_script = f"""
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SetOutputToWaveFile('{out_wav.replace("'", "''")}')
$synth.Speak('{text.replace("'", "''")}')
$synth.SetOutputToDefaultAudioDevice()
"""
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_script],
            capture_output=True, text=True, timeout=30
        )
        return os.path.isfile(out_wav)
    except Exception as e:
        logger.error(f'TTS error: {e}')
        return False

# ─── HTTP Handler ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    ipod_path = None  # set at startup

    def log_message(self, format, *args):
        pass  # suppress default logging

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/' or path == '/index.html':
            self.send_html(HTML_PAGE)
        elif path == '/api/songs':
            songs = scan_songs(self.ipod_path)
            self.send_json(songs)
        elif path == '/api/playlists':
            playlists = scan_playlists(self.ipod_path)
            self.send_json(playlists)
        elif path == '/api/logs':
            self.send_json({'logs': logger.get_logs()})
        elif path == '/api/storage':
            total, used, free = shutil.disk_usage(self.ipod_path)
            self.send_json({'total': total, 'used': used, 'free': free})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/api/playlist/save':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
            name = body.get('name', '').strip()
            tracks = body.get('tracks', [])
            if not name:
                self.send_json({'error': 'Name required'}, 400)
                return
            filepath = save_playlist(self.ipod_path, name, tracks)
            logger.info(f'Saved playlist "{name}" with {len(tracks)} tracks')
            self.send_json({'ok': True, 'path': filepath})

        elif path == '/api/playlist/delete':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
            name = body.get('name', '').strip()
            ok = delete_playlist(self.ipod_path, name)
            if ok: logger.info(f'Deleted playlist "{name}"')
            self.send_json({'ok': ok})

        elif path == '/api/songs/delete':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
            paths = body.get('paths', [])
            deleted_count = 0
            for rel_path in paths:
                full_path = os.path.normpath(os.path.join(self.ipod_path, rel_path.replace('/', os.sep)))
                # Security check: must be inside ipod_path
                if full_path.startswith(self.ipod_path) and os.path.isfile(full_path):
                    try:
                        os.remove(full_path)
                        deleted_count += 1
                        logger.info(f'Deleted song: {rel_path}')
                    except Exception as e:
                        logger.error(f'Error deleting {rel_path}: {e}')
            self.send_json({'ok': True, 'deleted': deleted_count})

        elif path == '/api/upload':
            # Multipart file upload — staged only, NOT synced until user presses Sync
            content_type = self.headers.get('Content-Type', '')
            ctype, pdict = cgi.parse_header(content_type)
            if ctype != 'multipart/form-data':
                self.send_json({'error': 'multipart required'}, 400)
                return
            if 'boundary' not in pdict:
                self.send_json({'error': 'no boundary'}, 400)
                return
            pdict['boundary'] = pdict['boundary'].encode('ascii')
            length = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(length)
            pdict['CONTENT-LENGTH'] = length
            fields = cgi.parse_multipart(io.BytesIO(raw), pdict)
            added = []
            # Stage to hidden .staging folder — auto-deleted on exit/startup
            dest_dir = os.path.join(self.ipod_path, 'iPod_Control', 'Music', STAGING_FOLDER)
            os.makedirs(dest_dir, exist_ok=True)
            filenames = fields.get('filename', [])
            filedata = fields.get('file', [])
            for fname, fdata in zip(filenames, filedata):
                fname = os.path.basename(fname)
                if not fname:
                    continue
                dest = os.path.join(dest_dir, fname)
                base, ext = os.path.splitext(fname)
                counter = 1
                while os.path.exists(dest):
                    dest = os.path.join(dest_dir, f'{base}_{counter}{ext}')
                    counter += 1
                with open(dest, 'wb') as f:
                    f.write(fdata if isinstance(fdata, bytes) else fdata.encode())
                logger.info(f'Staged (pending sync): {fname}')
                rel = os.path.relpath(dest, self.ipod_path).replace(os.sep, '/')
                added.append(rel)
            self.send_json({'ok': True, 'added': added})

        elif path == '/api/sync':
            # Stream sync output
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()

            def write_chunk(text):
                data = text.encode('utf-8')
                header = f'{len(data):X}\r\n'.encode()
                try:
                    self.wfile.write(header + data + b'\r\n')
                    self.wfile.flush()
                except Exception:
                    pass

            # ── Step 1: commit staged files to permanent locations ──────────────
            staging_dir = os.path.join(self.ipod_path, 'iPod_Control', 'Music', STAGING_FOLDER)
            if os.path.isdir(staging_dir):
                staged_files = [
                    f for f in os.listdir(staging_dir)
                    if os.path.splitext(f)[1].lower() in AUDIO_EXT
                ]
                if staged_files:
                    write_chunk(f'=== Committing {len(staged_files)} staged song(s) to iPod ===\n')
                    for fname in staged_files:
                        src = os.path.join(staging_dir, fname)
                        # Determine destination folder from metadata
                        artist, album = 'Unknown Artist', 'Unknown Album'
                        if MUTAGEN:
                            try:
                                audio = mutagen.File(src, easy=True)
                                if audio:
                                    artist = audio.get('artist', [artist])[0]
                                    album = audio.get('album', [album])[0]
                            except Exception:
                                pass
                        # Sanitise folder names for FAT filesystem
                        def safe(s): return re.sub(r'[<>:"/\\|?*]', '_', s).strip() or 'Unknown'
                        dest_dir_song = os.path.join(
                            self.ipod_path, 'iPod_Control', 'Music',
                            safe(artist), safe(album)
                        )
                        os.makedirs(dest_dir_song, exist_ok=True)
                        dest = os.path.join(dest_dir_song, fname)
                        base, ext = os.path.splitext(fname)
                        counter = 1
                        while os.path.exists(dest):
                            dest = os.path.join(dest_dir_song, f'{base}_{counter}{ext}')
                            counter += 1
                        shutil.move(src, dest)
                        write_chunk(f'  ✅ {fname} → {os.path.relpath(dest, self.ipod_path)}\n')
                    # Clean up empty staging directory
                    cleanup_staging(self.ipod_path)

            # ── Step 2: generate voiceover WAVs ──────────────────────────────────
            write_chunk('\n=== Generating Windows TTS voiceover for playlists ===\n')
            speakable_playlists = os.path.join(self.ipod_path, 'iPod_Control', 'Speakable', 'Playlists')
            speakable_tracks = os.path.join(self.ipod_path, 'iPod_Control', 'Speakable', 'Tracks')
            os.makedirs(speakable_playlists, exist_ok=True)
            os.makedirs(speakable_tracks, exist_ok=True)

            playlists = scan_playlists(self.ipod_path)
            for pl in playlists:
                name = pl['name']
                dbid = hashlib.md5(name.encode('utf-8')).digest()[:8]
                fn = ''.join(format(x, '02x') for x in reversed(dbid))
                wav_path = os.path.join(speakable_playlists, fn + '.wav')
                if not os.path.isfile(wav_path):
                    write_chunk(f'  Voiceover: {name}\n')
                    ok = windows_tts_wav(name, wav_path)
                    if not ok:
                        write_chunk(f'  [Warning] TTS failed for: {name}\n')
                else:
                    write_chunk(f'  Voiceover cached: {name}\n')

            write_chunk('\n=== Running iPod Shuffle database sync ===\n')
            logger.info('Starting iPod sync...')
            try:
                proc = subprocess.Popen(
                    [sys.executable, SYNC_SCRIPT, '-p', self.ipod_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=SCRIPT_DIR
                )
                for line in proc.stdout:
                    write_chunk(line)
                proc.wait()
                if proc.returncode == 0:
                    write_chunk('\n✅ Sync complete! Safely eject and reconnect your iPod.\n')
                else:
                    write_chunk(f'\n❌ Sync exited with code {proc.returncode}\n')
            except Exception as e:
                write_chunk(f'\n❌ Error running sync: {e}\n')

            # End chunked transfer
            try:
                self.wfile.write(b'0\r\n\r\n')
                self.wfile.flush()
            except Exception:
                pass

        else:
            self.send_response(404)
            self.end_headers()


# ─── HTML Page ──────────────────────────────────────────────────────────────────
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>iPod Shuffle Manager</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #ffffff;
    --surface: #f7f7f7;
    --border: #e2e2e2;
    --text: #333333;
    --text2: #666666;
    --accent: #ff8c00;
    --accent-light: #fff3e0;
    --accent-hover: #ffa726;
    --green: #4ade80;
    --red: #f87171;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
  
  /* Header */
  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 20px;
    background: #ffffff;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .logo { display: flex; align-items: center; gap: 10px; font-weight: 600; font-size: 16px; color: var(--accent); }
  .header-actions { display: flex; gap: 8px; align-items: center; }

  /* Buttons */
  .btn {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 6px 12px; border-radius: 4px; font-size: 12px; font-weight: 500;
    border: 1px solid var(--border); background: #fff; cursor: pointer; color: var(--text);
  }
  .btn:hover { background: #f0f0f0; }
  .btn-primary { background: linear-gradient(180deg, #ffb74d, var(--accent)); color: white; border: 1px solid #e65100; }
  .btn-primary:hover { background: linear-gradient(180deg, #ffa726, #f57c00); }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .sidebar-btn { width: 22px; height: 22px; padding: 0 !important; display: flex; align-items: center; justify-content: center; font-size: 14px; border: none; background: transparent; }
  .sidebar-btn:hover { background: rgba(0,0,0,0.05); }

  /* Layout */
  .app { display: flex; flex: 1; min-height: 0; }
  
  /* Sidebar */
  .sidebar { width: 220px; background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; }
  .sidebar-section { padding: 12px 16px 4px; font-size: 11px; font-weight: 600; color: var(--text2); text-transform: uppercase; display: flex; justify-content: space-between; align-items: center; }
  .nav-item { padding: 8px 16px; cursor: pointer; display: flex; align-items: center; justify-content: space-between; font-size: 13px; color: var(--text); user-select: none; border-left: 3px solid transparent; }
  .nav-item:hover { background: #eaeaeb; }
  .nav-item.active { background: var(--accent-light); border-left-color: var(--accent); font-weight: 600; color: #d84315; }
  .nav-item.delete-ready:hover { background: #fee2e2; border-left-color: var(--red); color: var(--red); }
  .nav-icon { width: 20px; opacity: 0.6; }
  
  /* Main View */
  .main-view { flex: 1; display: flex; flex-direction: column; min-width: 0; background: #fff; }
  .toolbar { padding: 10px 16px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; background: #fcfcfc; }
  .search-input { border: 1px solid var(--border); border-radius: 12px; padding: 4px 10px; font-size: 12px; outline: none; width: 200px; }
  .search-input:focus { border-color: var(--accent); }
  
  /* Table Container */
  .table-container { flex: 1; overflow-y: auto; overflow-x: auto; }
  .song-table { width: 100%; border-collapse: collapse; text-align: left; font-size: 12px; table-layout: fixed; }
  .song-table th { 
    position: sticky; top: 0; background: #f3f3f3; padding: 6px 10px;
    color: var(--text2); font-weight: normal; border-bottom: 1px solid var(--border); border-right: 1px solid var(--border);
    cursor: pointer; user-select: none; z-index: 10; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .song-table td { padding: 4px 10px; border-bottom: 1px solid #f0f0f0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--text); }
  .song-table tr:nth-child(even) { background: #fbfbfb; }
  .song-table tr:hover { background: #eef3fa; }
  .song-table tr.selected { background: var(--accent-light); color: #000; }
  .song-table tr.selected td { border-bottom-color: #ffd0a0; }
  
  .col-type { width: 30px; text-align: center; color: var(--text2); }
  .col-pos { width: 40px; color: var(--text2); }
  .col-title { width: 30%; }
  .col-artist { width: 20%; color: var(--text2); }
  .col-album { width: 20%; color: var(--text2); }
  .col-rating { width: 80px; color: #ccc; }
  .col-time { width: 60px; color: var(--text2); text-align: right; }
  
  /* Bottom Action Bar */
  .action-bar { padding: 10px 16px; border-top: 1px solid var(--border); display: flex; gap: 8px; align-items: center; background: #fcfcfc; }
  .action-bar select { border: 1px solid var(--border); padding: 4px 8px; font-size: 12px; border-radius: 4px; outline: none; }

  /* Toast & Modals */
  .toast-container { position: fixed; bottom: 24px; right: 24px; display: flex; flex-direction: column; gap: 8px; z-index: 9999; }
  .toast { background: #333; color: #fff; padding: 10px 16px; border-radius: 6px; font-size: 12px; animation: slideIn 0.25s ease; box-shadow: 0 4px 12px rgba(0,0,0,0.2); }
  @keyframes slideIn { from { opacity:0; transform: translateY(20px); } to { opacity:1; transform: translateY(0); } }
  
  /* Row state highlighting */
  .song-table tr.row-staged td { background: #fff8e1 !important; }
  .song-table tr.row-staged:hover td { background: #fff3cd !important; }
  .song-table tr.row-duplicate td { background: #fff0f0 !important; }
  .song-table tr.row-duplicate:hover td { background: #ffe0e0 !important; }
  .song-table tr.row-staged-dup td { background: #fff0e0 !important; }
  .song-table tr.row-staged-dup:hover td { background: #ffe8cc !important; }
  .song-table tr.selected td { background: var(--accent-light) !important; }
  .row-badge { display:inline-block; font-size:9px; padding:1px 5px; border-radius:3px; margin-left:6px; font-weight:600; vertical-align:middle; }
  .badge-staged { background:#ffb300; color:#fff; }
  .badge-dup { background:#ef4444; color:#fff; }

  /* Legend */
  .legend { display:flex; gap:14px; align-items:center; padding:5px 16px; font-size:11px; color:var(--text2); border-bottom:1px solid var(--border); background:#fafafa; flex-shrink:0; }
  .legend-dot { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:4px; }
  .legend-staged { background:#ffb300; }
  .legend-dup { background:#ef4444; }
  .legend-both { background:#ff7043; }
  
  /* Utilities */
  .empty { padding: 40px; text-align: center; color: var(--text2); font-size: 13px; }
  .hidden { display: none !important; }</style>
</head>
<body>

<header>
  <div class="logo">
    <span style="font-size:20px;">🎶</span> iPod Manager
  </div>
  <div class="header-actions">
    <button class="btn" onclick="document.getElementById('fileInput').click()">➕ Add Songs</button>
    <input type="file" id="fileInput" multiple accept=".mp3,.m4a,.m4b,.wav" style="display:none" onchange="handleFiles(this.files)">
    <button class="btn" onclick="refresh()">↻ Refresh</button>
    <button class="btn btn-primary" id="syncBtn" onclick="startSync()">▶ Sync to iPod</button>
  </div>
</header>

<div class="app">
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sidebar-section">Library</div>
    <div class="nav-item active" id="nav-library" onclick="switchView('library')">
      <span><span class="nav-icon">📱</span> IPOD</span>
      <span id="nav-library-count" style="font-size:11px;color:var(--text2)"></span>
    </div>
    
    <div class="sidebar-section" style="margin-top:16px;">
      Playlists
      <div style="display:flex;gap:4px;">
        <button class="btn sidebar-btn" onclick="newPlaylist()" title="New Playlist">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
        </button>
        <button class="btn sidebar-btn" id="toggleDeleteBtn" onclick="toggleDeleteMode()" title="Toggle Delete Mode">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
        </button>
      </div>
    </div>
    <div id="playlistNavList">
      <!-- Playlists render here -->
    </div>
  </div>

  <!-- Main View -->
  <div class="main-view">
    <div class="toolbar">
      <div id="viewTitle" style="font-weight:600;font-size:14px;">Song Library</div>
      <div style="display:flex;gap:8px;">
        <input class="search-input" type="text" id="songSearch" placeholder="Search…" oninput="filterSongs()">
      </div>
    </div>
    
    <div id="uploadStatus" style="font-size:12px;padding:8px 16px;background:var(--accent-light);color:#d84315;display:none;"></div>
    
    <!-- Legend strip -->
    <div class="legend" id="legendStrip" style="display:none;">
      <span><span class="legend-dot legend-staged"></span>Pending sync</span>
      <span><span class="legend-dot legend-dup"></span>Duplicate</span>
      <span><span class="legend-dot legend-both"></span>Both</span>
    </div>

    <div class="table-container" id="songList"></div>

    <!-- Actions for selected songs -->
    <div class="action-bar" id="actionBar">
      <span style="font-size:12px;color:var(--text2);" id="selectionCount">0 selected</span>
      <span style="font-size:12px;color:var(--text2);margin-left:16px;" id="storageInfo"></span>
      <div style="flex:1;"></div>
      <select id="targetPlaylist"><option value="">— Select playlist —</option></select>
      <button class="btn" onclick="addSelectionToPlaylist()">+ Add to playlist</button>
      <button class="btn" id="removeFromPlaylistBtn" onclick="removeFromCurrentPlaylist()" style="display:none;color:var(--red);">− Remove from playlist</button>
      <button class="btn" id="deleteFromIpodBtn" onclick="deleteSelectedSongsFromIpod()" style="color:var(--red);">🗑 Delete from iPod</button>
    </div>

    <!-- Sync Progress -->
    <div id="syncStatus" style="font-size:12px;padding:12px 16px;background:#f0f9ff;border-top:1px solid #bae6fd;color:#0369a1;display:none;flex-shrink:0;">
      <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
        <span id="syncText" style="font-weight:600;">Syncing...</span>
      </div>
      <div style="width:100%;height:6px;background:#e0f2fe;border-radius:3px;overflow:hidden;">
        <div id="syncBar" style="width:0%;height:100%;background:#0ea5e9;transition:width 0.2s;"></div>
      </div>
    </div>
  </div>
</div>

<div class="toast-container" id="toastContainer"></div>

<script>
let allSongs = [];
let allPlaylists = [];
let selectedPaths = new Set();
let filteredSongs = [];
let currentView = 'library'; // 'library' or index
let deleteMode = false;
let pivotIndex = -1;
let sortCol = 'pos';
let sortDesc = false;

// ─── Data loading ───────────────────────────────────────────────────────────
async function loadSongs() {
  const res = await fetch('/api/songs');
  allSongs = await res.json();
  document.getElementById('nav-library-count').textContent = allSongs.length;
  filterSongs();
}

async function loadPlaylists() {
  const res = await fetch('/api/playlists');
  allPlaylists = await res.json();
  renderPlaylists();
  updatePlaylistSelect();
  autoRefreshView();
}

async function loadStorage() {
  try {
    const res = await fetch('/api/storage');
    const data = await res.json();
    const formatBytes = (bytes) => {
      if (bytes === 0) return '0B';
      const k = 1024, sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + sizes[i];
    };
    const freeStr = formatBytes(data.free);
    const totalStr = formatBytes(data.total);
    document.getElementById('storageInfo').innerHTML = `Available storage: ${freeStr} &nbsp;&nbsp; Available after sync: ${freeStr} &nbsp;&nbsp; Total storage size: ${totalStr}`;
  } catch(e) {}
}

function refresh() {
  loadSongs();
  loadPlaylists();
  loadStorage();
  toast('Refreshed!');
}

// ─── Navigation & Views ───────────────────────────────────────────────────────
function toggleDeleteMode() {
  deleteMode = !deleteMode;
  const btn = document.getElementById('toggleDeleteBtn');
  btn.style.color = deleteMode ? 'var(--red)' : 'var(--text)';
  btn.style.fontWeight = deleteMode ? 'bold' : 'normal';
  renderPlaylists();
  if (deleteMode) toast('Delete mode active. Click a playlist to remove it.', 'error');
}

function switchView(view) {
  if (deleteMode && typeof view === 'number') {
    deletePlaylistByIdx(view);
    return;
  }
  
  currentView = view;
  selectedPaths.clear();
  pivotIndex = -1;
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  
  const titleEl = document.getElementById('viewTitle');
  const rmBtn = document.getElementById('removeFromPlaylistBtn');
  
  if (view === 'library') {
    document.getElementById('nav-library').classList.add('active');
    titleEl.textContent = 'Song Library';
    rmBtn.style.display = 'none';
  } else {
    document.getElementById('nav-pl-' + view).classList.add('active');
    titleEl.textContent = allPlaylists[view].name;
    rmBtn.style.display = 'inline-flex';
  }
  
  filterSongs();
}

function autoRefreshView() {
  // if current view is a deleted playlist, go to library
  if (currentView !== 'library' && !allPlaylists[currentView]) {
    switchView('library');
  } else {
    switchView(currentView); // re-trigger render
  }
}

// ─── Songs rendering & selection ────────────────────────────────────────────────
function filterSongs() {
  const q = document.getElementById('songSearch').value.toLowerCase();
  
  let sourceSongs = allSongs;
  if (currentView !== 'library') {
    const plTracks = allPlaylists[currentView].tracks;
    sourceSongs = plTracks.map(t => allSongs.find(s => s.path === t) || { path: t, title: t.split('/').pop(), artist: '', album: '', duration: '00:00', pos: 0 });
  }

  filteredSongs = q ? sourceSongs.filter(s =>
    (s.title||'').toLowerCase().includes(q) || (s.artist||'').toLowerCase().includes(q) || (s.album||'').toLowerCase().includes(q)
  ) : [...sourceSongs];
  
  sortAndRenderSongs();
}

function sortSongs(col) {
  if (sortCol === col) {
    sortDesc = !sortDesc;
  } else {
    sortCol = col;
    sortDesc = false;
  }
  sortAndRenderSongs();
}

function sortAndRenderSongs() {
  filteredSongs.sort((a, b) => {
    let valA = a[sortCol] || '';
    let valB = b[sortCol] || '';
    
    if (sortCol === 'pos') {
      valA = parseInt(valA) || 0;
      valB = parseInt(valB) || 0;
    } else if (typeof valA === 'string') {
      valA = valA.toLowerCase();
      valB = valB.toLowerCase();
    }
    
    if (valA < valB) return sortDesc ? 1 : -1;
    if (valA > valB) return sortDesc ? -1 : 1;
    if (a.pos < b.pos) return -1;
    return 1;
  });
  
  renderSongs();
}

function renderSongs() {
  const el = document.getElementById('songList');
  if (!filteredSongs.length) {
    el.innerHTML = '<div class="empty">No songs found in this view.</div>';
    document.getElementById('selectionCount').textContent = '0 selected';
    document.getElementById('legendStrip').style.display = 'none';
    return;
  }
  
  const getSortIcon = (col) => sortCol === col ? (sortDesc ? ' ▼' : ' ▲') : '';

  // Show legend if any staged or duplicate rows exist
  const hasStaged = filteredSongs.some(s => s.staged);
  const hasDup = filteredSongs.some(s => s.duplicate_of && s.duplicate_of.length > 0);
  document.getElementById('legendStrip').style.display = (hasStaged || hasDup) ? 'flex' : 'none';

  let html = `<table class="song-table">
    <thead>
      <tr>
        <th class="col-type">🎵</th>
        <th class="col-pos" onclick="sortSongs('pos')">Pos${getSortIcon('pos')}</th>
        <th class="col-title" onclick="sortSongs('title')">Title${getSortIcon('title')}</th>
        <th class="col-artist" onclick="sortSongs('artist')">Artist${getSortIcon('artist')}</th>
        <th class="col-album" onclick="sortSongs('album')">Album${getSortIcon('album')}</th>
        <th class="col-rating">Rating</th>
        <th class="col-time" onclick="sortSongs('duration')">Time${getSortIcon('duration')}</th>
      </tr>
    </thead>
    <tbody>`;
    
  html += filteredSongs.map((s, idx) => {
    const isStaged = s.staged;
    const isDup = s.duplicate_of && s.duplicate_of.length > 0;
    let rowClass = selectedPaths.has(s.path) ? 'selected' : '';
    if (!selectedPaths.has(s.path)) {
      if (isStaged && isDup) rowClass = 'row-staged-dup';
      else if (isStaged) rowClass = 'row-staged';
      else if (isDup) rowClass = 'row-duplicate';
    }
    const badges = (isStaged ? '<span class="row-badge badge-staged">PENDING</span>' : '') +
                   (isDup    ? '<span class="row-badge badge-dup">DUP</span>'     : '');
    const dupTitle = isDup ? ` title="Duplicate of: ${escHtml(s.duplicate_of.join(', '))}"` : '';
    return `
    <tr class="${rowClass}" onclick="toggleSong('${escHtml(s.path)}', event, ${idx})" data-path="${escHtml(s.path)}">
      <td class="col-type">🎵</td>
      <td class="col-pos">${s.pos || ''}</td>
      <td class="col-title" title="${escHtml(s.title)}">${escHtml(s.title)}${badges}</td>
      <td class="col-artist" title="${escHtml(s.artist)}">${escHtml(s.artist)}</td>
      <td class="col-album"${dupTitle}>${escHtml(s.album)}</td>
      <td class="col-rating">★★★★★</td>
      <td class="col-time">${escHtml(s.duration) || '00:00'}</td>
    </tr>
  `;
  }).join('');
  
  html += `</tbody></table>`;
  el.innerHTML = html;
  
  document.getElementById('selectionCount').textContent = `${selectedPaths.size} selected`;
}

function toggleSong(path, event, index) {
  if (event.shiftKey && pivotIndex !== -1) {
    document.getSelection()?.removeAllRanges(); // prevent text selection
    selectedPaths.clear();
    const start = Math.min(pivotIndex, index);
    const end = Math.max(pivotIndex, index);
    for(let i = start; i <= end; i++) {
        selectedPaths.add(filteredSongs[i].path);
    }
  } else if (event.ctrlKey || event.metaKey) {
    if (selectedPaths.has(path)) selectedPaths.delete(path);
    else selectedPaths.add(path);
    pivotIndex = index;
  } else {
    selectedPaths.clear();
    selectedPaths.add(path);
    pivotIndex = index;
  }
  renderSongs();
}

// ─── Playlists Sidebar & Data ────────────────────────────────────────────────
function renderPlaylists() {
  const el = document.getElementById('playlistNavList');
  el.innerHTML = allPlaylists.map((pl, idx) => `
    <div class="nav-item ${currentView === idx ? 'active' : ''} ${deleteMode ? 'delete-ready' : ''}" 
         id="nav-pl-${idx}" 
         onclick="switchView(${idx})"
         ondblclick="event.stopPropagation(); renamePlaylistByIdx(${idx})"
         title="${deleteMode ? 'Click to delete' : 'Double-click to rename'}">
      <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${escHtml(pl.name)}">
        <span class="nav-icon">${deleteMode ? '🗑️' : '🎧'}</span> ${escHtml(pl.name)}
      </span>
      <span style="font-size:11px;color:var(--text2)">${pl.tracks.length}</span>
    </div>
  `).join('');
}

async function deletePlaylistByIdx(idx) {
  const pl = allPlaylists[idx];
  if (!confirm(`Delete playlist "${pl.name}"?`)) return;
  await fetch('/api/playlist/delete', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name: pl.name})
  });
  allPlaylists.splice(idx, 1);
  toast(`Deleted "${pl.name}"`, 'success');
  selectedPaths.clear();
  renderPlaylists();
  updatePlaylistSelect();
  if (currentView === idx) switchView('library');
  else if (currentView > idx && typeof currentView === 'number') currentView--; 
}

async function renamePlaylistByIdx(idx) {
  const old = allPlaylists[idx];
  const newName = prompt('New playlist name:', old.name);
  if (!newName || !newName.trim() || newName.trim() === old.name) return;
  
  await fetch('/api/playlist/delete', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name: old.name})
  });
  old.name = newName.trim();
  await fetch('/api/playlist/save', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name: old.name, tracks: old.tracks})
  });
  
  toast(`Renamed to "${old.name}"`, 'success');
  renderPlaylists();
  updatePlaylistSelect();
  if (currentView === idx) document.getElementById('viewTitle').textContent = old.name;
}

function updatePlaylistSelect() {
  const sel = document.getElementById('targetPlaylist');
  sel.innerHTML = '<option value="">— Select playlist —</option>' +
    allPlaylists.map((pl,i) => `<option value="${i}">${escHtml(pl.name)}</option>`).join('');
}

async function savePlaylist(idx) {
  const pl = allPlaylists[idx];
  const res = await fetch('/api/playlist/save', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name: pl.name, tracks: pl.tracks})
  });
  const data = await res.json();
  if (data.ok) toast(`Saved "${pl.name}"`, 'success');
  else toast('Save failed', 'error');
}

async function newPlaylist() {
  const name = prompt('Playlist name:');
  if (!name || !name.trim()) return;
  const pl = { name: name.trim(), tracks: [], path: '' };
  allPlaylists.push(pl);
  await fetch('/api/playlist/save', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name: pl.name, tracks: []})
  });
  await loadPlaylists();
  toast(`Created "${pl.name}"`, 'success');
}

async function deleteCurrentPlaylist() {
  if (currentView === 'library' || !allPlaylists[currentView]) return;
  const pl = allPlaylists[currentView];
  if (!confirm(`Delete playlist "${pl.name}"?`)) return;
  await fetch('/api/playlist/delete', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name: pl.name})
  });
  allPlaylists.splice(currentView, 1);
  toast(`Deleted "${pl.name}"`, 'success');
  selectedPaths.clear();
  renderPlaylists();
  updatePlaylistSelect();
  switchView('library');
}

async function renameCurrentPlaylist() {
  if (currentView === 'library' || !allPlaylists[currentView]) return;
  const old = allPlaylists[currentView];
  const newName = prompt('New playlist name:', old.name);
  if (!newName || !newName.trim() || newName.trim() === old.name) return;
  
  await fetch('/api/playlist/delete', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name: old.name})
  });
  old.name = newName.trim();
  await fetch('/api/playlist/save', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({name: old.name, tracks: old.tracks})
  });
  
  toast(`Renamed to "${old.name}"`, 'success');
  renderPlaylists();
  updatePlaylistSelect();
  switchView(currentView); // update title
}

async function removeFromCurrentPlaylist() {
  if (currentView === 'library' || !allPlaylists[currentView] || selectedPaths.size === 0) return;
  const pl = allPlaylists[currentView];
  const count = selectedPaths.size;
  pl.tracks = pl.tracks.filter(t => !selectedPaths.has(t));
  await savePlaylist(currentView);
  selectedPaths.clear();
  toast(`Removed ${count} song(s)`, 'success');
  autoRefreshView();
}

async function addSelectionToPlaylist() {
  const sel = document.getElementById('targetPlaylist');
  const idx = sel.value;
  if (idx === '' || !selectedPaths.size) {
    toast('Select songs and a playlist first', 'error');
    return;
  }
  const pl = allPlaylists[parseInt(idx)];
  let added = 0;
  for (const path of selectedPaths) {
    if (!pl.tracks.includes(path)) {
      pl.tracks.push(path);
      added++;
    }
  }
  await savePlaylist(parseInt(idx));
  renderPlaylists();
  if (currentView !== 'library') {
    autoRefreshView();
  }
  toast(`Added ${added} song(s) to "${pl.name}"`, 'success');
}

async function deleteSelectedSongsFromIpod() {
  if (!selectedPaths.size) {
    toast('Select songs to delete first', 'error');
    return;
  }
  
  if (!confirm(`Permanently delete ${selectedPaths.size} song(s) from your iPod? This cannot be undone.`)) {
    return;
  }
  
  try {
    const res = await fetch('/api/songs/delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ paths: Array.from(selectedPaths) })
    });
    const data = await res.json();
    if (data.ok) {
      toast(`Deleted ${data.deleted} song(s) from iPod`, 'success');
      selectedPaths.clear();
      refresh();
    } else {
      toast('Deletion failed', 'error');
    }
  } catch(e) {
    toast('Error: ' + e.message, 'error');
  }
}

// ─── Upload ───────────────────────────────────────────────────────────────────
async function handleFiles(files) {
  if (!files.length) return;
  const status = document.getElementById('uploadStatus');
  status.style.display = 'block';
  status.innerHTML = `<span style="color:#666">⏳ Staging ${files.length} file(s)…</span>`;

  const fd = new FormData();
  for (const f of files) {
    fd.append('file', f);
    fd.append('filename', f.name);
  }

  try {
    const res = await fetch('/api/upload', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.ok) {
      status.innerHTML = `<span style="color:#d97706">🟡 ${data.added.length} file(s) staged — they will be copied to your iPod when you press Sync.</span>`;
      toast(`${data.added.length} song(s) queued for sync`, 'success');
      await loadSongs();
      loadStorage();
    } else {
      status.innerHTML = `<span style="color:var(--red)">❌ Staging failed</span>`;
    }
  } catch(e) {
    status.innerHTML = `<span style="color:var(--red)">❌ Error: ${e.message}</span>`;
  }
}

const uploadZone = document.getElementById('uploadZone');
uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('drag-over'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-over'));
uploadZone.addEventListener('drop', e => {
  e.preventDefault();
  uploadZone.classList.remove('drag-over');
  handleFiles(e.dataTransfer.files);
});

// ─── Sync ─────────────────────────────────────────────────────────────────────
async function startSync() {
  const btn = document.getElementById('syncBtn');
  const syncStatusEl = document.getElementById('syncStatus');
  const syncText = document.getElementById('syncText');
  const syncBar = document.getElementById('syncBar');

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span style="font-size:10px;">⏳</span> Syncing…';
  }

  syncStatusEl.style.display = 'block';
  syncBar.style.background = '#0ea5e9';
  syncBar.style.width = '0%';
  syncText.textContent = 'Initializing sync...';
  // Hide the upload status banner now that we're syncing
  document.getElementById('uploadStatus').style.display = 'none';

  try {
    const res = await fetch('/api/sync', { method: 'POST' });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    
    let simulatedProgress = 0;
    const progressInterval = setInterval(() => {
      if (simulatedProgress < 90) {
        simulatedProgress += Math.random() * 5;
        syncBar.style.width = `${Math.min(90, simulatedProgress)}%`;
      }
    }, 500);

    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      const text = decoder.decode(value, {stream:true});
      const lines = text.trim().split('\n').map(l => l.trim()).filter(l => l);
      if (lines.length > 0) {
         syncText.textContent = lines[lines.length - 1];
      }
    }
    
    clearInterval(progressInterval);
    syncBar.style.width = '100%';
    syncBar.style.background = '#10b981';
    syncText.textContent = '✅ Sync complete! Safely eject your iPod.';
    await loadSongs(); // refresh so staged rows turn back to normal
    
    setTimeout(() => {
        syncStatusEl.style.display = 'none';
        syncBar.style.width = '0%';
    }, 5000);

  } catch(e) {
    syncBar.style.width = '100%';
    syncBar.style.background = '#ef4444'; // Red on error
    syncText.textContent = '❌ Connection error: ' + e.message;
  }
  
  if (btn) {
    btn.disabled = false;
    btn.innerHTML = '▶ Sync to iPod';
  }
  await loadPlaylists();
  loadStorage();
}



// ─── Toast ────────────────────────────────────────────────────────────────────
function toast(msg, type='success') {
  const c = document.getElementById('toastContainer');
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.innerHTML = (type==='success'?'✅':'❌') + ' ' + msg;
  c.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

// ─── Utils ────────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// ─── Logs ───────────────────────────────────────────────────────────────────
async function loadLogs() {
  try {
    const res = await fetch('/api/logs');
    const data = await res.json();
    const el = document.getElementById('syncConsole');
    // If we're not currently syncing, show the persistent logs
    const syncBtn = document.getElementById('syncBtn');
    if (el && syncBtn && !syncBtn.disabled) {
      el.textContent = data.logs || 'No logs yet.';
      el.scrollTop = el.scrollHeight;
    }
  } catch(e) {}
}

// ─── Init ─────────────────────────────────────────────────────────────────────
loadSongs();
loadPlaylists();
loadStorage();
loadLogs();
setInterval(loadLogs, 5000);
</script>
</body>
</html>
"""

# ─── Main ───────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print("Usage: python ipod_manager.py <path-to-ipod>")
        print("Example: python ipod_manager.py E:\\")
        sys.exit(1)

    ipod_path = os.path.abspath(sys.argv[1])
    if not os.path.isdir(ipod_path):
        print(f"Error: {ipod_path} is not a directory")
        sys.exit(1)

    music_dir = os.path.join(ipod_path, 'iPod_Control', 'Music')
    if not os.path.isdir(music_dir):
        print(f"Error: Could not find iPod_Control/Music in {ipod_path}")
        print("Is the iPod mounted?")
        sys.exit(1)

    if not MUTAGEN:
        print("Warning: mutagen not installed. Song titles/artists won't be read from ID3 tags.")

    # Clean up any leftover staging from a previous crash
    cleanup_staging(ipod_path)

    Handler.ipod_path = ipod_path

    server = HTTPServer(('127.0.0.1', PORT), Handler)
    url = f'http://localhost:{PORT}'
    print(f"✅ iPod Manager running at {url}")
    print(f"   iPod path: {ipod_path}")
    print("   Press Ctrl+C to stop.")

    # Open browser after short delay
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        # Always delete staged files if the user never pressed Sync
        cleanup_staging(ipod_path)
        print("Staged files cleared. Goodbye.")


if __name__ == '__main__':
    main()
