#!/usr/bin/env python3
"""
Upload prepped sets from /root/soundcloud/ to the Ronaut Radio SoundCloud
account via the official API.

Expects per set (made by sc_prep.py):  <name>.m4a  <name>.txt  <name>.jpg
Auth lives in /root/soundcloud/sc_auth.json:
  {"client_id": ..., "client_secret": ..., "access_token": ..., "refresh_token": ...}
Access tokens last ~1h; this script refreshes automatically and rewrites the file.

Uploads default to PRIVATE so they can be reviewed before publishing.
Publish flow: re-run later with --publish <track_id ...> or --publish-all.

Usage:
  python3 sc_upload.py                       # upload every prepped set (private)
  python3 sc_upload.py Ronaut_010_-Thurs-Night   # specific set(s), by file stem
  python3 sc_upload.py --public ...          # upload straight to public
  python3 sc_upload.py --publish-all         # flip every uploaded track public
"""

import glob
import json
import os
import re
import subprocess
import sys

SC_DIR = "/root/soundcloud"
AUTH_PATH = os.path.join(SC_DIR, "sc_auth.json")
UPLOADED_PATH = os.path.join(SC_DIR, "uploaded.json")
TRACKLIST_DIR = "/root/tracklists"
API = "https://api.soundcloud.com"
TOKEN_URL = "https://secure.soundcloud.com/oauth/token"


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def refresh_token(auth):
    r = subprocess.run([
        "curl", "-s", "-X", "POST", TOKEN_URL,
        "--data-urlencode", "grant_type=refresh_token",
        "--data-urlencode", f"client_id={auth['client_id']}",
        "--data-urlencode", f"client_secret={auth['client_secret']}",
        "--data-urlencode", f"refresh_token={auth['refresh_token']}",
    ], capture_output=True, text=True)
    data = json.loads(r.stdout)
    if "access_token" not in data:
        print(f"TOKEN REFRESH FAILED: {r.stdout[:300]}")
        sys.exit(1)
    auth["access_token"] = data["access_token"]
    auth["refresh_token"] = data.get("refresh_token", auth["refresh_token"])
    save_json(AUTH_PATH, auth)
    return auth


def api_curl(auth, args, retry=True):
    """Run a curl call with auth; refresh token and retry once on 401."""
    cmd = ["curl", "-s", "-w", "\n%{http_code}",
           "-H", f"Authorization: OAuth {auth['access_token']}"] + args
    r = subprocess.run(cmd, capture_output=True, text=True)
    body, _, status = r.stdout.rpartition("\n")
    if status == "401" and retry:
        refresh_token(auth)
        return api_curl(auth, args, retry=False)
    return body, status


def normalize(name):
    base = re.sub(r"^ronaut[\[_]\d+[\]_]*\s*[-_ ]*", "", name.strip(), flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", "", base.lower())


def genre_for(stem):
    norm = normalize(stem)
    for cand in (f"{norm}.json", f"{norm}_tracklist.json"):
        path = os.path.join(TRACKLIST_DIR, cand)
        if os.path.exists(path):
            genres = load_json(path, {}).get("genres") or []
            if genres:
                return genres[0].title()
    return "Electronic"


def set_number(stem):
    m = re.search(r"Ronaut[\[_](\d+)", stem, re.IGNORECASE)
    return m.group(1) if m else ""


def title_for(stem):
    """Prefer the real set title from the description's intro line, else the file stem."""
    base = ""
    desc_p = os.path.join(SC_DIR, stem + ".txt")
    if os.path.exists(desc_p):
        first = open(desc_p).readline()
        base = first.split(" — recorded live")[0].strip()
    if not base:
        base = re.sub(r"^Ronaut[\[_]\d+[\]_]*\s*[-_]*", "", stem)
        base = re.sub(r"[_]+", " ", base).strip(" -")
    num = set_number(stem)
    return f"{base} — Ronaut [{num}]" if num else base


def upload(auth, stem, public=False):
    audio = os.path.join(SC_DIR, stem + ".m4a")
    desc_p = os.path.join(SC_DIR, stem + ".txt")
    art = os.path.join(SC_DIR, stem + ".jpg")
    if not os.path.exists(audio):
        print(f"SKIP {stem}: no audio")
        return

    uploaded = load_json(UPLOADED_PATH, {})
    if stem in uploaded:
        print(f"SKIP {stem}: already uploaded -> {uploaded[stem]['permalink_url']}")
        return

    desc = open(desc_p).read() if os.path.exists(desc_p) else ""
    title = title_for(stem)
    genre = genre_for(stem)
    sharing = "public" if public else "private"
    print(f"=== {stem}\n  title: {title} | genre: {genre} | {sharing}")

    args = ["-X", "POST", f"{API}/tracks",
            "-F", f"track[title]={title}",
            "-F", f"track[description]={desc}",
            "-F", f"track[sharing]={sharing}",
            "-F", f"track[genre]={genre}",
            "-F", 'track[tag_list]=vinyl "dj set" "los angeles" "ronaut radio" radio',
            "-F", f"track[asset_data]=@{audio}"]
    if os.path.exists(art):
        args += ["-F", f"track[artwork_data]=@{art}"]

    body, status = api_curl(auth, args)
    if status not in ("200", "201"):
        print(f"  FAILED HTTP {status}: {body[:300]}")
        return
    track = json.loads(body)
    uploaded[stem] = {
        "id": track.get("id"),
        "permalink_url": track.get("permalink_url"),
        "sharing": sharing,
    }
    save_json(UPLOADED_PATH, uploaded)
    print(f"  OK -> {track.get('permalink_url')}")


def refresh_artwork(auth, stems=None):
    """PUT new artwork onto already-uploaded tracks (after a branding change)."""
    uploaded = load_json(UPLOADED_PATH, {})
    targets = stems or list(uploaded)
    for stem in targets:
        info = uploaded.get(stem)
        if not info or info.get("revoked"):
            continue
        art = os.path.join(SC_DIR, stem + ".jpg")
        if not os.path.exists(art):
            print(f"  no artwork file for {stem}")
            continue
        body, status = api_curl(auth, [
            "-X", "PUT", f"{API}/tracks/{info['id']}",
            "-F", f"track[artwork_data]=@{art}"])
        if status == "200":
            print(f"artwork updated: {stem}")
        else:
            print(f"FAILED artwork {stem}: HTTP {status} {body[:160]}")


def publish_all(auth):
    uploaded = load_json(UPLOADED_PATH, {})
    for stem, info in uploaded.items():
        if info.get("sharing") == "public" or info.get("revoked"):
            continue
        body, status = api_curl(auth, [
            "-X", "PUT", f"{API}/tracks/{info['id']}",
            "-F", "track[sharing]=public"])
        if status == "200":
            info["sharing"] = "public"
            print(f"published: {stem} -> {info['permalink_url']}")
        else:
            print(f"FAILED to publish {stem}: HTTP {status} {body[:200]}")
    save_json(UPLOADED_PATH, uploaded)


if __name__ == "__main__":
    auth = load_json(AUTH_PATH, None)
    if not auth:
        print(f"Missing {AUTH_PATH}")
        sys.exit(1)

    argv = sys.argv[1:]
    public = "--public" in argv
    argv = [a for a in argv if a != "--public"]

    if argv and argv[0] == "--publish-all":
        publish_all(auth)
        sys.exit(0)

    if argv and argv[0] == "--refresh-artwork":
        refresh_artwork(auth, argv[1:] or None)
        sys.exit(0)

    stems = argv or sorted(
        os.path.basename(p)[:-4] for p in glob.glob(os.path.join(SC_DIR, "*.m4a")))
    for stem in stems:
        upload(auth, stem, public=public)
