#!/usr/bin/env python3
"""
Regularize Music2 metadata: fill in missing tags from directory/filename structure.
Dest structure: Music2/<artist>/<year> - <album>/<tracknum> <trackname>.ext

Only fills in MISSING tags; never overwrites existing metadata.

Usage:
    /tmp/music_venv/bin/python3 regularize_dest.py          # dry run
    /tmp/music_venv/bin/python3 regularize_dest.py --apply   # write changes
"""

import os
import sys
import re
from pathlib import Path

from mutagen.mp4 import MP4
from mutagen.mp3 import MP3
from mutagen.id3 import ID3NoHeaderError
from mutagen.easyid3 import EasyID3

DEST = Path('/Users/kevin/Music2')
AUDIO_EXT = frozenset({'.mp3', '.m4a', '.m4p'})
MAX_TRACK_NUM = 25  # numbers above this in filenames are likely part of the title
DRY_RUN = '--apply' not in sys.argv


def is_audio(p):
    return p.suffix.lower() in AUDIO_EXT


def parse_album_dir_name(dirname):
    """Parse 'YYYY - Album Name' → (year, album).  Returns (None, dirname) if no year."""
    m = re.match(r'^(\d{4})\s*-\s*(.+)$', dirname)
    return (m.group(1), m.group(2).strip()) if m else (None, dirname)


def parse_track_filename(stem):
    """
    Extract (disc, track_num, title) from a filename stem.
    Uses the ≤25 rule: leading numbers > MAX_TRACK_NUM are part of the title.
    Returns (None, None, stem) if no track number detected.
    """
    # Pattern 1: disc-track  e.g. "1-07 Friction", "2-01 Hey You", "1-05 - Name"
    m = re.match(r'^(\d+)-(\d{1,3})\s*[-._]?\s*(.+)$', stem)
    if m:
        t = int(m.group(2))
        if t <= MAX_TRACK_NUM:
            return int(m.group(1)), t, m.group(3).strip()

    # Pattern 2: track only  e.g. "11 - Big Gun", "08 Heartbreaker", "01.Name"
    m = re.match(r'^(\d{1,3})\s*[-._]?\s*(.+)$', stem)
    if m:
        t = int(m.group(1))
        if t <= MAX_TRACK_NUM:
            return None, t, m.group(2).strip()

    # No recognisable track number
    return None, None, stem


# ── Tag I/O helpers ──────────────────────────────────────────────────────────

def read_tags(filepath):
    """Read existing tags. Returns (tag_dict, format_str, mutagen_obj) or Nones."""
    ext = filepath.suffix.lower()
    try:
        if ext in ('.m4a', '.m4p'):
            f = MP4(str(filepath))
            t = f.tags or {}
            return {
                'title':        _first(t.get('\xa9nam')),
                'artist':       _first(t.get('\xa9ART')),
                'album':        _first(t.get('\xa9alb')),
                'album_artist': _first(t.get('aART')),
                'track':        _first(t.get('trkn')),   # (num, total) or None
                'disc':         _first(t.get('disk')),
                'date':         _first(t.get('\xa9day')),
            }, 'mp4', f
        elif ext == '.mp3':
            try:
                f = MP3(str(filepath), ID3=EasyID3)
            except ID3NoHeaderError:
                f = MP3(str(filepath))
                f.add_tags(ID3=EasyID3)
            t = f.tags or {}
            return {
                'title':        _first(t.get('title')),
                'artist':       _first(t.get('artist')),
                'album':        _first(t.get('album')),
                'album_artist': _first(t.get('albumartist')),
                'track':        _first(t.get('tracknumber')),
                'disc':         _first(t.get('discnumber')),
                'date':         _first(t.get('date')),
            }, 'mp3', f
    except Exception as e:
        print(f'  WARN: cannot read {filepath}: {e}', file=sys.stderr)
    return None, None, None


def _first(lst):
    """Return first element of a list/tuple or None."""
    if lst and len(lst) > 0:
        return lst[0]
    return None


def write_tags(f, fmt, changes):
    """Apply tag changes to a mutagen object (call .save() after)."""
    if fmt == 'mp4':
        if f.tags is None:
            f.add_tags()
        mapping = {
            'title': '\xa9nam', 'artist': '\xa9ART', 'album': '\xa9alb',
            'album_artist': 'aART', 'date': '\xa9day',
        }
        for field, value in changes.items():
            if field in mapping:
                f.tags[mapping[field]] = [value]
            elif field == 'track':
                f.tags['trkn'] = [value]  # (num, total)
            elif field == 'disc':
                f.tags['disk'] = [value]
    elif fmt == 'mp3':
        if f.tags is None:
            f.add_tags(ID3=EasyID3)
        mapping = {
            'title': 'title', 'artist': 'artist', 'album': 'album',
            'album_artist': 'albumartist', 'date': 'date',
        }
        for field, value in changes.items():
            if field in mapping:
                f.tags[mapping[field]] = [str(value)]
            elif field in ('track', 'disc'):
                key = 'tracknumber' if field == 'track' else 'discnumber'
                f.tags[key] = [str(value)]


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    mode = 'APPLY' if not DRY_RUN else 'DRY RUN'
    print(f'=== Regularize Music2 metadata ({mode}) ===\n')

    total_files = 0
    files_changed = 0
    total_additions = 0

    for artist_dir in sorted(DEST.iterdir()):
        if not artist_dir.is_dir():
            continue
        dir_artist = artist_dir.name

        for item in sorted(artist_dir.iterdir()):
            if item.is_dir():
                year, album_name = parse_album_dir_name(item.name)
                audio_files = sorted(f for f in item.iterdir() if f.is_file() and is_audio(f))

                for pos, af in enumerate(audio_files, 1):
                    total_files += 1
                    disc, track_num, title = parse_track_filename(af.stem)
                    if track_num is None:
                        track_num = pos  # fallback to listing position

                    existing, fmt, fobj = read_tags(af)
                    if existing is None:
                        continue

                    changes = {}
                    if not existing['title']:
                        changes['title'] = title
                    if not existing['artist']:
                        changes['artist'] = dir_artist
                    if not existing['album']:
                        changes['album'] = album_name
                    if not existing['album_artist']:
                        changes['album_artist'] = dir_artist
                    if not existing['track']:
                        if fmt == 'mp4':
                            changes['track'] = (track_num, 0)
                        else:
                            changes['track'] = str(track_num)
                    if disc is not None and not existing['disc']:
                        if fmt == 'mp4':
                            changes['disc'] = (disc, 0)
                        else:
                            changes['disc'] = str(disc)
                    if year and not existing['date']:
                        changes['date'] = year

                    if changes:
                        files_changed += 1
                        total_additions += len(changes)
                        print(f'  {af.relative_to(DEST)}')
                        for field, value in sorted(changes.items()):
                            print(f'    + {field} = {value}')
                        if not DRY_RUN:
                            write_tags(fobj, fmt, changes)
                            fobj.save()

            elif item.is_file() and is_audio(item):
                # Anomaly: track directly under artist dir (no album subfolder)
                total_files += 1
                disc, track_num, title = parse_track_filename(item.stem)
                existing, fmt, fobj = read_tags(item)
                if existing is None:
                    continue

                changes = {}
                if not existing['title']:
                    changes['title'] = title
                if not existing['artist']:
                    changes['artist'] = dir_artist
                if not existing['album_artist']:
                    changes['album_artist'] = dir_artist
                if track_num is not None and not existing['track']:
                    if fmt == 'mp4':
                        changes['track'] = (track_num, 0)
                    else:
                        changes['track'] = str(track_num)

                if changes:
                    files_changed += 1
                    total_additions += len(changes)
                    print(f'  {item.relative_to(DEST)}')
                    for field, value in sorted(changes.items()):
                        print(f'    + {field} = {value}')
                    if not DRY_RUN:
                        write_tags(fobj, fmt, changes)
                        fobj.save()

    print(f'\n=== SUMMARY ===')
    print(f'  Files scanned:         {total_files}')
    print(f'  Files needing tags:    {files_changed}')
    print(f'  Total tag additions:   {total_additions}')
    if DRY_RUN:
        print('  (DRY RUN — no changes written. Use --apply to write.)')
    else:
        print('  Changes written to disk.')


if __name__ == '__main__':
    main()
