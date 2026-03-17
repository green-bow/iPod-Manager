#!/usr/bin/env python3
# import_playlists.py
# One-time script to import playlists exported from CopyTrans onto the iPod.
# Run AFTER songs have been copied to the iPod.
# Usage: python import_playlists.py

import os
import re
import sys
import io

# Force UTF-8 output to prevent Windows console crashes on characters like 'ć'
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ─── Config ────────────────────────────────────────────────────────────────────
IPOD_PATH     = os.path.abspath('E:/')
SOURCE_SONGS  = os.path.join('C:/Users/franc/Desktop/copy trans songs', 'Music')
COPYTRANS_DIR = 'C:/Users/franc/Desktop/copy trans songs'
IPOD_MUSIC    = os.path.join(IPOD_PATH, 'iPod_Control', 'Music')
PLAYLIST_DEST = IPOD_MUSIC  # .m3u files go here

# Playlists to import (skip 'saudade' and 'you')
PLAYLISTS_TO_IMPORT = ["classical", "elf", "fo", "kitsune"]

# ─── Helpers ───────────────────────────────────────────────────────────────────
def resolve_track(raw_path):
    """
    Given a path from the .m3u8 (relative to the CopyTrans export folder),
    find the corresponding file on the iPod.
    raw_path example: Music\Radiohead\In Rainbows\All I Need.mp3
    """
    # Strip the leading "Music\" prefix if present
    rel = raw_path.strip().replace('/', os.sep)
    if rel.lower().startswith('music' + os.sep):
        rel = rel[len('music') + 1:]  # e.g. Radiohead\In Rainbows\All I Need.mp3

    ipod_full = os.path.join(IPOD_MUSIC, rel)
    if os.path.isfile(ipod_full):
        return ipod_full

    # Try case-insensitive search (Windows doesn't need it but be safe)
    # Walk the iPod music dir to find a matching file
    fname = os.path.basename(rel)
    for dirpath, _, filenames in os.walk(IPOD_MUSIC):
        for f in filenames:
            if f.lower() == fname.lower():
                candidate = os.path.join(dirpath, f)
                return candidate
    return None


def parse_m3u8(filepath):
    """Parse an .m3u8 file and return list of (display_name, raw_path) tuples."""
    tracks = []
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    
    current_name = None
    for line in lines:
        line = line.strip()
        if line.startswith('#EXTINF:'):
            # #EXTINF:duration,Artist - Title
            parts = line.split(',', 1)
            current_name = parts[1] if len(parts) > 1 else None
        elif line and not line.startswith('#'):
            tracks.append((current_name, line))
            current_name = None
    return tracks


def write_m3u(name, resolved_tracks):
    """Write a .m3u playlist file to the iPod."""
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', name)
    out_path = os.path.join(PLAYLIST_DEST, safe_name + '.m3u')
    
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('#EXTM3U\n')
        for track_path in resolved_tracks:
            # Write path relative to the playlist file location (IPOD_MUSIC)
            rel = os.path.relpath(track_path, PLAYLIST_DEST)
            f.write(rel + '\n')
    
    return out_path


# ─── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("iPod Playlist Importer")
    print("=" * 60)
    
    if not os.path.isdir(IPOD_MUSIC):
        print(f"ERROR: iPod Music folder not found at: {IPOD_MUSIC}")
        print("Make sure the iPod is connected as E:\\ and songs have been copied.")
        sys.exit(1)
    
    total_imported = 0
    total_missing = 0
    
    for pl_name in PLAYLISTS_TO_IMPORT:
        # Try both .m3u8 extensions
        src = None
        for ext in ['.m3u8', '.m3u']:
            candidate = os.path.join(COPYTRANS_DIR, pl_name + ext)
            if os.path.isfile(candidate):
                src = candidate
                break
        
        if not src:
            print(f"\n[!] Playlist file not found: {pl_name}")
            continue
        
        print(f"\n[+] Processing playlist: {pl_name}")
        tracks_raw = parse_m3u8(src)
        
        resolved = []
        missing = []
        for display, raw in tracks_raw:
            ipod_path = resolve_track(raw)
            if ipod_path:
                resolved.append(ipod_path)
                title = display or os.path.basename(raw)
                print(f"   [OK] {title}")
            else:
                missing.append(raw)
                print(f"   [X]  NOT FOUND: {raw}")
        
        if resolved:
            out = write_m3u(pl_name, resolved)
            print(f"\n   → Written: {out}")
            print(f"   → {len(resolved)} tracks imported, {len(missing)} missing")
        else:
            print(f"   → No tracks found, skipping playlist.")
        
        total_imported += len(resolved)
        total_missing += len(missing)
    
    print(f"\n{'=' * 60}")
    print(f"Done! {total_imported} tracks across {len(PLAYLISTS_TO_IMPORT)} playlists.")
    if total_missing:
        print(f"[!] {total_missing} tracks could not be found on the iPod.")
        print("   These songs may not have been copied yet.")
    print(f"\nNext step: Run 'launch_ipod_manager.bat' and click 'Sync to iPod'.")
    print("=" * 60)


if __name__ == '__main__':
    main()
