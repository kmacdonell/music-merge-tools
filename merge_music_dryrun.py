#!/tmp/music_venv/bin/python3
"""
Dry-run: identify missing music to merge from Music/ and Music_backup/ into Music2/.
Metadata tags are the primary source for artist/album/track; directory names are fallback.
Dest structure: <artist>/<year> - <album>/<tracknum> <trackname>.ext
"""

import os
import re
import subprocess
import json
import sys
import shutil
from collections import defaultdict
from pathlib import Path
from mutagen.mp4 import MP4
from mutagen.mp3 import MP3
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError

MAX_TRACK_NUM = 25  # leading numbers above this are part of the title

AUDIO_EXT = frozenset({
    '.mp3', '.m4a', '.m4p', '.flac', '.aac', '.wav', '.aiff', '.aif', '.ogg', '.wma',
})

DEST = Path('/Users/kevin/Music')
SOURCES = [Path('/Volumes/usbshare1/Music')]

# iTunes/macOS hierarchy dirs that are NOT artist names
STRUCTURAL_DIRS = frozenset({
    'iTunes', 'iTunes Media', 'Music', 'Apple Music', 'Media.localized',
    'Audio Music Apps', 'GarageBand', 'Automatically Add to Music.localized',
    'Automatically Add to iTunes.localized', 'Automatically Add To TV.localized',
    'Home Videos',
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize(name):
    """Lowercase, strip filesystem-special chars, collapse whitespace."""
    s = name.lower().strip()
    s = re.sub(r'[/\\:*?"<>|_]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def strip_year_prefix(album_dir_name):
    """Strip leading 'YYYY - ' from a dest album directory name."""
    m = re.match(r'^\d{4}\s*-\s*(.+)$', album_dir_name)
    return m.group(1).strip() if m else album_dir_name


def parse_track_filename(stem):
    """
    Extract (disc, track_num, title) from a filename stem.
    Leading numbers > MAX_TRACK_NUM are treated as part of the title.
    Returns (None, None, stem) if no track number detected.
    """
    # Pattern 1: disc-track  e.g. "1-07 Friction", "2-01 Hey You"
    m = re.match(r'^(\d+)-(\d{1,3})\s*[-._]?\s*(.+)$', stem)
    if m:
        t = int(m.group(2))
        if t <= MAX_TRACK_NUM:
            return int(m.group(1)), t, m.group(3).strip()
    # Pattern 2: track only  e.g. "11 - Big Gun", "08 Heartbreaker", "06 Circle"
    m = re.match(r'^(\d{1,3})\s*[-._]?\s*(.+)$', stem)
    if m:
        t = int(m.group(1))
        if t <= MAX_TRACK_NUM:
            return None, t, m.group(2).strip()
    return None, None, stem


def strip_track_number(filename):
    """Convenience: return just the title portion of a filename."""
    _, _, title = parse_track_filename(Path(filename).stem)
    return title


def is_audio(path):
    return Path(path).suffix.lower() in AUDIO_EXT


def is_drm(path):
    """Return True if the file is DRM-protected (Apple .m4p)."""
    return Path(path).suffix.lower() == '.m4p'


def get_metadata(filepath):
    """Extract tags + audio stream info via ffprobe."""
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_format', '-show_streams', '-select_streams', 'a:0',
             str(filepath)],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(r.stdout)
        tags = data.get('format', {}).get('tags', {})
        fmt = data.get('format', {})
        streams = data.get('streams', [])
        stream = streams[0] if streams else {}
        return {
            'title': tags.get('title') or tags.get('TITLE'),
            'artist': tags.get('artist') or tags.get('ARTIST'),
            'album_artist': tags.get('album_artist') or tags.get('ALBUMARTIST'),
            'album': tags.get('album') or tags.get('ALBUM'),
            'date': tags.get('date') or tags.get('DATE') or tags.get('originaldate'),
            'track': tags.get('track') or tags.get('TRACKNUMBER'),
            'bitrate': stream.get('bit_rate') or fmt.get('bit_rate'),
            'duration': stream.get('duration') or fmt.get('duration'),
        }
    except Exception as e:
        print(f"  WARN ffprobe failed: {filepath}: {e}", file=sys.stderr)
        return {}


def extract_year(date_str):
    """Pull a 4-digit year from a date string like '2009-11-10' or '2009'."""
    if not date_str:
        return None
    m = re.search(r'\b(\d{4})\b', date_str)
    return m.group(1) if m else None


def sanitize_for_fs(name):
    """Replace chars that are unsafe in filenames."""
    return re.sub(r'[/\\:*?"<>|]', '_', name)


# ---------------------------------------------------------------------------
# Build destination maps
# ---------------------------------------------------------------------------

def _read_dest_tags(filepath):
    """Read title/artist/album from a dest file via mutagen. Returns dict."""
    ext = filepath.suffix.lower()
    try:
        if ext in ('.m4a', '.m4p'):
            f = MP4(str(filepath))
            t = f.tags or {}
            first = lambda k: (t.get(k) or [None])[0]
            return {'title': first('\xa9nam'), 'artist': first('\xa9ART'),
                    'album': first('\xa9alb'), 'album_artist': first('aART')}
        elif ext == '.mp3':
            try:
                f = MP3(str(filepath), ID3=EasyID3)
            except ID3NoHeaderError:
                return {}
            t = f.tags or {}
            first = lambda k: (t.get(k) or [None])[0]
            return {'title': first('title'), 'artist': first('artist'),
                    'album': first('album'), 'album_artist': first('albumartist')}
    except Exception:
        pass
    return {}


def build_dest_maps(dest_dir):
    """
    Walk Music2/<artist>/<year - album>/<track> and build:
      map1  {norm_artist: {norm_album: {norm_track: entry}}}
      map2  {norm_album: [entries]}
    where entry = (artist_dir, album_dir, track_filename, full_path)

    Uses mutagen metadata for titles when available; falls back to filename.
    Indexes by both metadata-derived AND directory-derived keys.
    """
    map1 = defaultdict(lambda: defaultdict(dict))
    map2 = defaultdict(list)

    def _add(na, nalb, nt, entry):
        map1[na][nalb][nt] = entry
        map2[nalb].append((nt,) + entry)

    for artist_dir in sorted(dest_dir.iterdir()):
        if not artist_dir.is_dir():
            continue
        raw_artist = artist_dir.name
        norm_dir_artist = normalize(raw_artist)

        for item in sorted(artist_dir.iterdir()):
            if item.is_dir():
                raw_album_dir = item.name
                dir_album_name = strip_year_prefix(raw_album_dir)
                norm_dir_album = normalize(dir_album_name)

                audio_files = sorted(f for f in item.iterdir()
                                     if f.is_file() and is_audio(f))
                for pos, tf in enumerate(audio_files, 1):
                    tags = _read_dest_tags(tf)
                    _, _, file_title = parse_track_filename(tf.stem)

                    track_name = tags.get('title') or file_title
                    norm_track = normalize(track_name)
                    entry = (raw_artist, raw_album_dir, tf.name, tf)

                    # Primary key: metadata-derived artist + album
                    meta_artist = tags.get('album_artist') or tags.get('artist')
                    meta_album = tags.get('album')
                    norm_meta_artist = normalize(meta_artist) if meta_artist else norm_dir_artist
                    norm_meta_album = normalize(meta_album) if meta_album else norm_dir_album

                    _add(norm_meta_artist, norm_meta_album, norm_track, entry)

                    # Also index by directory-derived keys for cross-matching
                    if norm_meta_artist != norm_dir_artist:
                        _add(norm_dir_artist, norm_meta_album, norm_track, entry)
                    if norm_meta_album != norm_dir_album:
                        _add(norm_meta_artist, norm_dir_album, norm_track, entry)
                        if norm_meta_artist != norm_dir_artist:
                            _add(norm_dir_artist, norm_dir_album, norm_track, entry)

            elif item.is_file() and is_audio(item):
                tags = _read_dest_tags(item)
                _, _, file_title = parse_track_filename(item.stem)
                track_name = tags.get('title') or file_title
                norm_track = normalize(track_name)
                entry = (raw_artist, '', item.name, item)
                _add(norm_dir_artist, '', norm_track, entry)

    return map1, map2


# ---------------------------------------------------------------------------
# Enumerate source tracks
# ---------------------------------------------------------------------------

def find_source_tracks(source_dirs):
    """Walk sources, return deduplicated list of audio files with dir context."""
    tracks = []
    seen = set()

    for src in source_dirs:
        for root, _dirs, files in os.walk(src):
            for f in files:
                fp = Path(root) / f
                if not is_audio(fp):
                    continue
                key = (f, fp.stat().st_size)
                if key in seen:
                    continue
                seen.add(key)

                parent = fp.parent.name
                grandparent = fp.parent.parent.name
                tracks.append({
                    'path': fp,
                    'filename': f,
                    'dir_album': parent,
                    'dir_artist': grandparent if grandparent not in STRUCTURAL_DIRS else None,
                })
    return tracks


# ---------------------------------------------------------------------------
# Identity resolution (metadata-first)
# ---------------------------------------------------------------------------

def resolve_identity(track, meta):
    """Return (artist, album, title, year) using metadata first, dirs as fallback."""
    title = meta.get('title') or strip_track_number(track['filename'])
    album = meta.get('album') or track['dir_album']
    artist = meta.get('album_artist') or meta.get('artist') or track['dir_artist']
    year = extract_year(meta.get('date'))
    return artist, album, title, year


# ---------------------------------------------------------------------------
# Duplicate check
# ---------------------------------------------------------------------------

_meta_cache = {}

def cached_metadata(path):
    key = str(path)
    if key not in _meta_cache:
        _meta_cache[key] = get_metadata(path)
    return _meta_cache[key]


def check_duplicate(src_meta, dest_path):
    """
    Compare bitrate and audio-data size (bitrate × duration).
    Returns True (dup), False (different), or None (inconclusive).
    """
    dst = cached_metadata(dest_path)
    src_br, dst_br = src_meta.get('bitrate'), dst.get('bitrate')
    src_dur, dst_dur = src_meta.get('duration'), dst.get('duration')

    if not all([src_br, dst_br, src_dur, dst_dur]):
        return None
    try:
        src_br_i, dst_br_i = int(src_br), int(dst_br)
        src_dur_f, dst_dur_f = float(src_dur), float(dst_dur)
        br_match = abs(src_br_i - dst_br_i) < 1000          # within 1 kbps
        dur_match = abs(src_dur_f - dst_dur_f) < 1.0         # within 1 s
        src_sz = src_br_i * src_dur_f
        dst_sz = dst_br_i * dst_dur_f
        sz_match = abs(src_sz - dst_sz) / max(src_sz, dst_sz, 1) < 0.05
        return br_match and dur_match and sz_match
    except (ValueError, ZeroDivisionError):
        return None


def should_replace(src_meta, dest_path, src_path):
    """
    Decide whether the source should replace the dest track.
    Rule 1: non-DRM always replaces DRM.
    Rule 2: higher bitrate replaces lower bitrate.
    Returns (replace: bool, reason: str).
    """
    src_is_drm = is_drm(src_path)
    dst_is_drm = is_drm(dest_path)

    if dst_is_drm and not src_is_drm:
        return True, 'non-DRM replaces DRM'
    if src_is_drm and not dst_is_drm:
        return False, 'dest is non-DRM, source is DRM'

    # Same DRM status — compare bitrates
    dst_meta = cached_metadata(dest_path)
    src_br = src_meta.get('bitrate')
    dst_br = dst_meta.get('bitrate')
    if src_br and dst_br:
        try:
            src_br_i = int(src_br)
            dst_br_i = int(dst_br)
            if src_br_i > dst_br_i + 1000:  # meaningfully higher (> 1 kbps)
                return True, f'higher bitrate ({src_br_i // 1000}kbps > {dst_br_i // 1000}kbps)'
        except ValueError:
            pass
    return False, 'no improvement'


# ---------------------------------------------------------------------------
# Propose dest path for new content
# ---------------------------------------------------------------------------

def propose_dest_path(artist, album, year, filename, map1):
    """Build the target path under Music2, reusing existing dir names when possible."""
    norm_a = normalize(artist) if artist else ''

    # Reuse existing artist directory name if we can match it
    if norm_a and norm_a in map1:
        any_entry = next(iter(next(iter(map1[norm_a].values())).values()))
        artist_dir = any_entry[0]
    else:
        artist_dir = sanitize_for_fs(artist) if artist else '[unknown artist]'

    # Reuse existing album directory name if artist+album matched
    norm_alb = normalize(album) if album else ''
    if norm_a in map1 and norm_alb in map1[norm_a]:
        any_entry = next(iter(map1[norm_a][norm_alb].values()))
        album_dir = any_entry[1]
    else:
        # Build new album dir in dest convention: "YYYY - Album"
        safe_album = sanitize_for_fs(album) if album else '[unknown album]'
        album_dir = f"{year} - {safe_album}" if year else safe_album

    return DEST / artist_dir / album_dir / filename


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    apply = '--apply' in sys.argv
    mode = 'APPLY' if apply else 'DRY RUN'
    print('=' * 70)
    print(f'MUSIC MERGE — {mode}')
    print('=' * 70)

    # --- Build dest maps ---
    print('\n[1/3] Building destination maps from Music2 ...')
    map1, map2 = build_dest_maps(DEST)
    n_artists = len(map1)
    n_albums = sum(len(a) for a in map1.values())
    n_tracks = sum(len(t) for a in map1.values() for t in a.values())
    print(f'  map1: {n_artists} artists, {n_albums} albums, {n_tracks} tracks')
    print(f'  map2: {len(map2)} unique normalized album names')

    # --- Scan sources ---
    print('\n[2/3] Scanning sources & reading metadata (ffprobe) ...')
    source_tracks = find_source_tracks(SOURCES)
    print(f'  {len(source_tracks)} unique audio files in sources')

    # --- Classify each source track ---
    duplicates = []
    collisions = []   # name match, different audio
    upgrades  = []   # source is non-DRM, dest is DRM — prefer non-DRM
    would_copy = []
    unmatched = []

    for i, trk in enumerate(source_tracks, 1):
        meta = get_metadata(trk['path'])
        artist, album, title, year = resolve_identity(trk, meta)

        norm_artist = normalize(artist) if artist else ''
        norm_album = normalize(album) if album else ''
        norm_title = normalize(title) if title else ''

        matched = False

        # --- Strategy 1: map1 (artist + album) ---
        if norm_artist and norm_artist in map1:
            if norm_album in map1[norm_artist]:
                album_tracks = map1[norm_artist][norm_album]
                if norm_title in album_tracks:
                    entry = album_tracks[norm_title]
                    replace, reason = should_replace(meta, entry[3], trk['path'])
                    rec = {
                        'source': str(trk['path']),
                        'dest': str(entry[3]),
                        'artist': artist, 'album': album, 'title': title,
                        'via': 'map1 (artist+album+title)',
                    }
                    if replace:
                        src_ext = trk['path'].suffix.lower()
                        dst_ext = entry[3].suffix.lower()
                        upgrade_dest = entry[3] if src_ext == dst_ext \
                            else entry[3].parent / (entry[3].stem + src_ext)
                        upgrades.append({
                            'source': str(trk['path']),
                            'dest': str(upgrade_dest),
                            'old_file': str(entry[3]),
                            'artist': artist, 'album': album, 'title': title,
                            'via': rec['via'], 'reason': reason,
                        })
                    else:
                        dup = check_duplicate(meta, entry[3])
                        if dup is False:
                            collisions.append(rec)
                        else:
                            rec['conclusive'] = dup is True
                            duplicates.append(rec)
                    matched = True
                else:
                    # existing album, new track
                    dp = propose_dest_path(artist, album, year, trk['filename'], map1)
                    would_copy.append({
                        'source': str(trk['path']), 'dest': str(dp),
                        'artist': artist, 'album': album, 'title': title,
                        'via': 'map1 (artist+album exist, new track)',
                    })
                    matched = True
            else:
                # artist exists, new album
                dp = propose_dest_path(artist, album, year, trk['filename'], map1)
                would_copy.append({
                    'source': str(trk['path']), 'dest': str(dp),
                    'artist': artist, 'album': album, 'title': title,
                    'via': 'map1 (artist exists, new album)',
                })
                matched = True

        # --- Strategy 2: map2 (album only) ---
        if not matched and norm_album and norm_album in map2:
            entries = map2[norm_album]
            for (nt, da, dad, df, dp) in entries:
                if nt == norm_title:
                    replace, reason = should_replace(meta, dp, trk['path'])
                    rec = {
                        'source': str(trk['path']),
                        'dest': str(dp),
                        'artist': artist, 'album': album, 'title': title,
                        'via': f'map2 (album+title, dest_artist={da})',
                    }
                    if replace:
                        src_ext = trk['path'].suffix.lower()
                        dst_ext = dp.suffix.lower()
                        upgrade_dest = dp if src_ext == dst_ext \
                            else dp.parent / (dp.stem + src_ext)
                        upgrades.append({
                            'source': str(trk['path']),
                            'dest': str(upgrade_dest),
                            'old_file': str(dp),
                            'artist': artist, 'album': album, 'title': title,
                            'via': rec['via'], 'reason': reason,
                        })
                    else:
                        dup = check_duplicate(meta, dp)
                        if dup is False:
                            collisions.append(rec)
                        else:
                            rec['conclusive'] = dup is True
                            duplicates.append(rec)
                    matched = True
                    break
            if not matched:
                # album exists under some artist, track is new
                dest_artist_dir = entries[0][1]
                dest_album_dir = entries[0][2]
                dp = DEST / dest_artist_dir / dest_album_dir / trk['filename']
                would_copy.append({
                    'source': str(trk['path']), 'dest': str(dp),
                    'artist': artist, 'album': album, 'title': title,
                    'via': f'map2 (album exists under {dest_artist_dir}, new track)',
                })
                matched = True

        # --- Strategy 3: completely new ---
        if not matched:
            dp = propose_dest_path(artist, album, year, trk['filename'], map1)
            unmatched.append({
                'source': str(trk['path']),
                'proposed_dest': str(dp),
                'artist': artist, 'album': album, 'title': title,
            })

        if i % 25 == 0:
            print(f'  ... processed {i}/{len(source_tracks)}')

    # --- Report ---
    print(f'\n{"=" * 70}')
    print('DRY RUN RESULTS')
    print('=' * 70)

    print(f'\n--- DUPLICATES (skip): {len(duplicates)} ---')
    for d in duplicates:
        tag = 'CONFIRMED' if d.get('conclusive') else 'PROBABLE'
        print(f'  [{tag}] {d["source"]}')
        print(f'    meta: artist={d["artist"]}  album={d["album"]}  title={d["title"]}')
        print(f'    matches: {d["dest"]}')
        print(f'    via: {d["via"]}')

    print(f'\n--- REPLACEMENTS (non-DRM or higher bitrate): {len(upgrades)} ---')
    for u in upgrades:
        print(f'  REPLACE: {u["source"]}')
        print(f'    meta: artist={u["artist"]}  album={u["album"]}  title={u["title"]}')
        print(f'    reason: {u["reason"]}')
        print(f'    replaces: {u["old_file"]}')
        print(f'    -> {u["dest"]}')
        print(f'    via: {u["via"]}')

    print(f'\n--- NAME COLLISIONS (same name, different audio): {len(collisions)} ---')
    for c in collisions:
        print(f'  COLLISION: {c["source"]}')
        print(f'    meta: artist={c["artist"]}  album={c["album"]}  title={c["title"]}')
        print(f'    vs: {c["dest"]}')
        print(f'    via: {c["via"]}')

    print(f'\n--- WOULD COPY: {len(would_copy)} ---')
    for c in would_copy:
        print(f'  COPY: {c["source"]}')
        print(f'    meta: artist={c["artist"]}  album={c["album"]}  title={c["title"]}')
        print(f'    -> {c["dest"]}')
        print(f'    via: {c["via"]}')

    print(f'\n--- UNMATCHED (entirely new): {len(unmatched)} ---')
    for u in unmatched:
        print(f'  NEW: {u["source"]}')
        print(f'    meta: artist={u["artist"]}  album={u["album"]}  title={u["title"]}')
        print(f'    proposed: {u["proposed_dest"]}')

    # --- Execute copies if --apply ---
    copied = 0
    copy_errors = []
    if apply:
        print(f'\n--- COPYING FILES ---')
        all_copies = [(c['source'], c['dest'], None) for c in would_copy] + \
                     [(u['source'], u['proposed_dest'], None) for u in unmatched] + \
                     [(g['source'], g['dest'], g['old_file']) for g in upgrades]
        for src, dst, old_file in all_copies:
            try:
                dst_path = Path(dst)
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied += 1
                print(f'  OK: {dst_path.relative_to(DEST)}')
                # Delete old file only if it differs from dest (different extension = different path)
                if old_file and old_file != dst:
                    Path(old_file).unlink()
                    print(f'  DEL: {Path(old_file).relative_to(DEST)}')
            except Exception as e:
                copy_errors.append((src, dst, str(e)))
                print(f'  FAIL: {dst} — {e}')

    print(f'\n{"=" * 70}')
    print('SUMMARY')
    print(f'  {len(duplicates):>5}  duplicates (skipped)')
    print(f'  {len(upgrades):>5}  replacements (non-DRM or higher bitrate)')
    print(f'  {len(collisions):>5}  name collisions (skipped)')
    print(f'  {len(would_copy):>5}  would copy (to existing albums)')
    print(f'  {len(unmatched):>5}  unmatched (new content)')
    print(f'  {len(source_tracks):>5}  total source tracks')
    if apply:
        print(f'  {copied:>5}  files copied successfully')
        if copy_errors:
            print(f'  {len(copy_errors):>5}  copy errors')
    else:
        print('  (DRY RUN — use --apply to copy files)')
    print('=' * 70)


if __name__ == '__main__':
    main()
