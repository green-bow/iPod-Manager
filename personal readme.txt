command prompt as administrator

Run ipod-shuffle-4g.py D:\ to compile everything you put in the ipod root directory (probably D:/ drive)

usage: ipod-shuffle-4g.py [-h] [-t] [-p] [-u] [-g TRACK_GAIN] [-d [AUTO_DIR_PLAYLISTS]] [-i [ID3_TEMPLATE]] [-v] path

positional arguments:
  path                  Path to the IPod's root directory

optional arguments:
  -h, --help            show this help message and exit
  -t, --track-voiceover
                        Enable track voiceover feature
  -p, --playlist-voiceover
                        Enable playlist voiceover feature
  -u, --rename-unicode  Rename files causing unicode errors, will do minimal
                        required renaming
  -g TRACK_GAIN, --track-gain TRACK_GAIN
                        Specify volume gain (0-99) for all tracks; 0 (default)
                        means no gain and is usually fine; e.g. 60 is very
                        loud even on minimal player volume
  -d [AUTO_DIR_PLAYLISTS], --auto-dir-playlists [AUTO_DIR_PLAYLISTS]
                        Generate automatic playlists for each folder
                        recursively inside "IPod_Control/Music/". You can
                        optionally limit the depth: 0=root, 1=artist, 2=album,
                        n=subfoldername, default=-1 (No Limit).
  -i [ID3_TEMPLATE], --auto-id3-playlists [ID3_TEMPLATE]
                        Generate automatic playlists based on the id3 tags of
                        any music added to the iPod. You can optionally
                        specify a template string based on which id3 tags are
                        used to generate playlists. For eg. '{artist} -
                        {album}' will use the pair of artist and album to
                        group tracks under one playlist. Similarly '{genre}'
                        will group tracks based on their genre tag. Default
                        template used is '{artist}'
  -v, --verbose         Show verbose output of database generation.