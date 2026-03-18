import os
import glob

music_path = r"E:\iPod_Control\Music"

# Build a lookup of all real file paths on disk, keyed by lowercase filename
real_files = {}
for dirpath, dirnames, filenames in os.walk(music_path):
    for filename in filenames:
        if filename.lower().endswith(('.mp3', '.m4a', '.m4b', '.wav')):
            full = os.path.join(dirpath, filename)
            real_files[filename.lower()] = full

for m3u_path in glob.glob(os.path.join(music_path, "*.m3u")):
    print(f"\nRebuilding: {m3u_path}")
    
    # Read as latin-1 — this never fails, maps bytes 1:1
    with open(m3u_path, 'r', encoding='latin-1') as f:
        lines = f.readlines()
    
    fixed = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('#') or stripped == '':
            fixed.append(line)
            continue
        
        # Get just the filename part to look up the real path
        filename = os.path.basename(stripped).lower()
        
        if filename in real_files:
            fixed.append(real_files[filename] + '\n')
            print(f"  OK: {real_files[filename]}")
        else:
            # Keep original line but warn
            fixed.append(line)
            print(f"  MISSING: {stripped}")
    
    with open(m3u_path, 'w', encoding='utf-8') as f:
        f.writelines(fixed)

print("\nDone. Run ipod-shuffle-4g.py now.")