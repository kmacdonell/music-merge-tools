# Music Merge Tools

Scripts for merging missing music from source directories into `~/Music2`.

## Prerequisites

- Python 3 with mutagen: `/tmp/music_venv/bin/python3`
- ffprobe (from ffmpeg): used for source file metadata and duplicate detection

If the venv is gone, recreate it:

```
python3 -m venv /tmp/music_venv
/tmp/music_venv/bin/pip install mutagen
```

## Scripts

### merge_music_dryrun.py

Identifies missing tracks in source directories and copies them into `~/Music2`.

**How it works:**

1. Builds a map of all tracks in `~/Music2` using mutagen metadata (title, artist, album) with directory/filename as fallback.
2. Scans source directories, reading metadata via ffprobe.
3. For each source track, resolves artist/album/title (metadata first, directory names as fallback).
4. Matches against the dest map by normalized artist + album + title.
5. Classifies each track as: duplicate, name collision, would-copy, or unmatched (new).
6. Duplicates are confirmed by comparing bitrate and audio data size.

**Track number stripping:** Leading numbers ≤ 25 in filenames are treated as track numbers and stripped to get the title. Numbers > 25 are kept as part of the title. Disc-track patterns like `1-07` are also handled.

**Dest structure:** `~/Music2/<artist>/<year> - <album>/<tracknum> <trackname>.ext`

**Usage:**

```
# Dry run — shows what would be copied
/tmp/music_venv/bin/python3 ~/merge_music_dryrun.py

# Apply — actually copies files
/tmp/music_venv/bin/python3 ~/merge_music_dryrun.py --apply
```

**To add new source directories**, edit the `SOURCES` list near the top of the script:

```python
SOURCES = [Path('/Users/kevin/Music'), Path('/Users/kevin/Music_backup')]
```

### regularize_dest.py

Fills in missing metadata tags on `~/Music2` files based on their directory/filename placement. Only adds tags that are absent — never overwrites existing metadata.

**Tags set from directory structure:**

- artist, album_artist → from artist directory name
- album → from album directory name (year prefix stripped)
- title → from filename (track number stripped)
- track number → from filename leading digits
- disc number → from disc-track filename patterns (e.g. `2-01`)
- date → year from album directory name

**Usage:**

```
# Dry run — shows what tags would be added
/tmp/music_venv/bin/python3 ~/regularize_dest.py

# Apply — writes tags to files
/tmp/music_venv/bin/python3 ~/regularize_dest.py --apply
```

**Run this before merging** if new files have been added to `~/Music2` without proper tags.

## Typical Workflow

1. Run `regularize_dest.py --apply` to ensure dest metadata is complete.
2. Update `SOURCES` in `merge_music_dryrun.py` if needed.
3. Run `merge_music_dryrun.py` (dry run) and review the output.
4. Run `merge_music_dryrun.py --apply` to copy files.
