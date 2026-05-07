#!/usr/bin/env python3
"""
Find albums in ~/Music that are NOT in Byron's collection and copy them
to a new folder on the USB drive.

Comparison is by normalized artist + album name (album year prefix stripped).

Usage:
    python3 export_missing_albums.py          # dry run
    python3 export_missing_albums.py --apply  # copy albums
"""

import os
import re
import sys
import shutil
from pathlib import Path

SRC = Path('/Users/kevin/Music')
REF = Path("/Volumes/usbshare8-2/Byron's Ripped CD's 050326/Music/Flac")
DEST = Path("/Volumes/usbshare8-2/Kevin's Music 050626/Music")

APPLY = '--apply' in sys.argv


def normalize(name):
    """Lowercase, strip special chars, collapse whitespace."""
    s = name.lower().strip()
    s = re.sub(r'[/\\:*?"<>|_\'\.,]', ' ', s)
    # Move leading "The " to end for matching: "The B-52s" -> "b-52s the"
    s = re.sub(r'^the\s+', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def strip_year_prefix(album_dir_name):
    """Strip leading 'YYYY - ' from album directory name."""
    m = re.match(r'^\d{4}\s*-\s*(.+)$', album_dir_name)
    return m.group(1).strip() if m else album_dir_name


def build_ref_albums(ref_dir):
    """Build set of (norm_artist, norm_album) from Byron's collection."""
    albums = set()
    for artist_dir in ref_dir.iterdir():
        if not artist_dir.is_dir():
            continue
        norm_artist = normalize(artist_dir.name)
        for album_dir in artist_dir.iterdir():
            if not album_dir.is_dir():
                continue
            norm_album = normalize(album_dir.name)
            albums.add((norm_artist, norm_album))
    return albums


def main():
    mode = 'APPLY' if APPLY else 'DRY RUN'
    print(f'=== Export Missing Albums ({mode}) ===\n')

    print('[1/3] Indexing Byron\'s collection ...')
    ref_albums = build_ref_albums(REF)
    print(f'  {len(ref_albums)} artist/album pairs')

    print('\n[2/3] Scanning ~/Music for albums not in Byron\'s ...')
    missing = []       # (src_artist_dir, src_album_dir, norm_artist, norm_album)
    already_have = 0

    for artist_dir in sorted(SRC.iterdir()):
        if not artist_dir.is_dir():
            continue
        norm_artist = normalize(artist_dir.name)

        for album_dir in sorted(artist_dir.iterdir()):
            if not album_dir.is_dir():
                continue
            album_name = strip_year_prefix(album_dir.name)
            norm_album = normalize(album_name)

            if (norm_artist, norm_album) in ref_albums:
                already_have += 1
            else:
                missing.append((artist_dir, album_dir, norm_artist, norm_album))

    print(f'  {already_have} albums already in Byron\'s collection')
    print(f'  {len(missing)} albums to export')

    # Report
    print(f'\n{"=" * 70}')
    print(f'ALBUMS TO EXPORT: {len(missing)}')
    print('=' * 70)
    for _, album_dir, _, _ in missing:
        print(f'  {album_dir.relative_to(SRC)}')

    # Copy
    copied = 0
    errors = []
    if APPLY:
        print(f'\n--- COPYING ---')
        for artist_dir, album_dir, _, _ in missing:
            dest_album = DEST / artist_dir.name / album_dir.name
            try:
                dest_album.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(str(album_dir), str(dest_album))
                copied += 1
                print(f'  OK: {album_dir.relative_to(SRC)}')
            except Exception as e:
                errors.append((str(album_dir), str(e)))
                print(f'  FAIL: {album_dir.relative_to(SRC)} — {e}')

    print(f'\n{"=" * 70}')
    print('SUMMARY')
    print(f'  {already_have:>5}  albums matched (skip)')
    print(f'  {len(missing):>5}  albums missing from Byron\'s')
    if APPLY:
        print(f'  {copied:>5}  albums copied')
        if errors:
            print(f'  {len(errors):>5}  errors')
    else:
        print('  (DRY RUN — use --apply to copy)')
    print('=' * 70)


if __name__ == '__main__':
    main()
