"""
Ronaut Radio — Discord Admin Bot
Manages residents, sets, and tracklists from a private #admin channel.

Setup:
  export DISCORD_BOT_TOKEN=your_token_here
  python3 discord_bot.py

Config: /root/discord_bot_config.json
  {
    "admin_channel_id": 1234567890,
    "authorized_user_ids": [111, 222]
  }

Invite URL scopes needed: bot, applications.commands
Bot permissions: Send Messages, Embed Links, Read Message History
"""

import json
import os
import re
import tempfile
import time as _time
from pathlib import Path

import discord
from discord import app_commands

# ── Paths ──────────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "discord_bot_config.json")
STAFF_PICKS_FILE = "/root/staff_picks.json"
RESIDENTS_FILE = "/root/residents.json"
TRACKLISTS_DIR = "/root/tracklists"


# ── Config ─────────────────────────────────────────────────────────────────
def _load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"admin_channel_id": 0, "authorized_user_ids": []}


# ── JSON helpers ───────────────────────────────────────────────────────────
def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    """Atomic write: write to tmp then rename to avoid corruption."""
    dir_ = os.path.dirname(path)
    with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False, suffix=".tmp") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        tmp_path = f.name
    os.replace(tmp_path, path)


# ── Name normalization + set resolution ───────────────────────────────────
def _normalize(name: str) -> str:
    """Strip Ronaut[N]- prefix, remove punctuation/spaces, lowercase."""
    base = os.path.basename(name or "")
    if base.lower().endswith(".mp4"):
        base = base[:-4]
    base = re.sub(r"^ronaut\[\d+\]\s*[-_ ]*", "", base.strip(), flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", "", base.lower())


def resolve_set(name: str):
    """
    Fuzzy-match *name* against staff_picks.json entries.
    Returns (index, entry) or (None, None) if not found.
    """
    try:
        picks = load_json(STAFF_PICKS_FILE)
    except (OSError, json.JSONDecodeError):
        return None, None

    needle = _normalize(name)
    for i, pick in enumerate(picks):
        fname = pick.get("filename", "")
        title = pick.get("title", "")
        if _normalize(fname) == needle or _normalize(title) == needle:
            return i, pick
    return None, None


def tracklist_path_for(set_name: str) -> str | None:
    """Return the tracklist JSON path for a set, or None if not found."""
    needle = _normalize(set_name)
    tl_dir = Path(TRACKLISTS_DIR)
    if not tl_dir.is_dir():
        return None
    for f in tl_dir.glob("*_tracklist.json"):
        if _normalize(f.stem.replace("_tracklist", "")) == needle:
            return str(f)
    return None


def _parse_timestamp(ts: str) -> int:
    """Parse 'mm:ss' or 'hh:mm:ss' into total seconds."""
    parts = ts.strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        pass
    raise ValueError(f"Invalid timestamp: {ts!r}. Use mm:ss or hh:mm:ss.")


def _fmt_time(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h}:{m:02}:{s:02}"
    return f"{m}:{s:02}"


# ── Embed helpers ──────────────────────────────────────────────────────────
def ok_embed(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(title=f"✅ {title}", description=description, color=0x2ECC71)


def err_embed(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(title=f"❌ {title}", description=description, color=0xE74C3C)


# ── Bot setup ──────────────────────────────────────────────────────────────
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


def _guard(interaction: discord.Interaction) -> bool:
    """Return True if the interaction is from the configured admin channel + authorized user."""
    cfg = _load_config()
    channel_ok = interaction.channel_id == cfg.get("admin_channel_id")
    auth_ids = cfg.get("authorized_user_ids", [])
    user_ok = not auth_ids or interaction.user.id in auth_ids
    return channel_ok and user_ok


# ── /resident-bio ──────────────────────────────────────────────────────────
@tree.command(name="resident-bio", description="Update a resident's bio")
@app_commands.describe(name="Resident name", bio="New bio text")
async def resident_bio(interaction: discord.Interaction, name: str, bio: str):
    if not _guard(interaction):
        return

    try:
        residents = load_json(RESIDENTS_FILE)
    except (OSError, json.JSONDecodeError) as e:
        await interaction.response.send_message(embed=err_embed("Cannot load residents", str(e)))
        return

    needle = name.strip().lower()
    for r in residents:
        if r.get("name", "").lower() == needle:
            r["bio"] = bio
            save_json(RESIDENTS_FILE, residents)
            await interaction.response.send_message(embed=ok_embed(
                f"Bio updated — {r['name']}",
                f"**New bio:**\n{bio}"
            ))
            return

    await interaction.response.send_message(embed=err_embed(
        "Resident not found",
        f"No resident named `{name}`. Check spelling."
    ))


# ── /resident-social ───────────────────────────────────────────────────────
@tree.command(name="resident-social", description="Set a social link for a resident")
@app_commands.describe(name="Resident name", platform="Platform (instagram/soundcloud/ra/etc)", url="URL")
async def resident_social(interaction: discord.Interaction, name: str, platform: str, url: str):
    if not _guard(interaction):
        return

    try:
        residents = load_json(RESIDENTS_FILE)
    except (OSError, json.JSONDecodeError) as e:
        await interaction.response.send_message(embed=err_embed("Cannot load residents", str(e)))
        return

    needle = name.strip().lower()
    for r in residents:
        if r.get("name", "").lower() == needle:
            if "social" not in r:
                r["social"] = {}
            r["social"][platform.lower()] = url
            save_json(RESIDENTS_FILE, residents)
            await interaction.response.send_message(embed=ok_embed(
                f"Social updated — {r['name']}",
                f"**{platform}:** {url}"
            ))
            return

    await interaction.response.send_message(embed=err_embed("Resident not found", f"No resident named `{name}`."))


# ── /resident-show ─────────────────────────────────────────────────────────
@tree.command(name="resident-show", description="Show current info for a resident")
@app_commands.describe(name="Resident name")
async def resident_show(interaction: discord.Interaction, name: str):
    if not _guard(interaction):
        return

    try:
        residents = load_json(RESIDENTS_FILE)
    except (OSError, json.JSONDecodeError) as e:
        await interaction.response.send_message(embed=err_embed("Cannot load residents", str(e)))
        return

    needle = name.strip().lower()
    for r in residents:
        if r.get("name", "").lower() == needle:
            socials = r.get("social", {})
            social_str = "\n".join(f"**{k}:** {v}" for k, v in socials.items()) or "None"
            embed = discord.Embed(title=r["name"], color=0x3498DB)
            embed.add_field(name="Bio", value=r.get("bio", "—"), inline=False)
            embed.add_field(name="Social", value=social_str, inline=False)
            embed.add_field(name="Photo", value=r.get("photo", "—"), inline=True)
            await interaction.response.send_message(embed=embed)
            return

    await interaction.response.send_message(embed=err_embed("Resident not found", f"No resident named `{name}`."))


# ── /sets-list ─────────────────────────────────────────────────────────────
@tree.command(name="sets-list", description="List all staff picks with titles")
async def sets_list(interaction: discord.Interaction):
    if not _guard(interaction):
        return

    try:
        picks = load_json(STAFF_PICKS_FILE)
    except (OSError, json.JSONDecodeError) as e:
        await interaction.response.send_message(embed=err_embed("Cannot load sets", str(e)))
        return

    lines = []
    for i, pick in enumerate(picks, 1):
        title = pick.get("title") or pick.get("filename", "?")
        fname = pick.get("filename", "")
        lines.append(f"**{i}.** {title} — `{fname}`")

    embed = discord.Embed(title="Staff Picks", description="\n".join(lines) or "No sets.", color=0x9B59B6)
    await interaction.response.send_message(embed=embed)


# ── /set-description ───────────────────────────────────────────────────────
@tree.command(name="set-description", description="Update a set's description")
@app_commands.describe(set="Set name (fuzzy)", description="New description")
async def set_description(interaction: discord.Interaction, set: str, description: str):
    if not _guard(interaction):
        return

    idx, pick = resolve_set(set)
    if pick is None:
        await interaction.response.send_message(embed=err_embed("Set not found", f"No match for `{set}`."))
        return

    picks = load_json(STAFF_PICKS_FILE)
    picks[idx]["description"] = description
    save_json(STAFF_PICKS_FILE, picks)

    await interaction.response.send_message(embed=ok_embed(
        f"Description updated — {pick.get('title', set)}",
        description
    ))


# ── /set-title ─────────────────────────────────────────────────────────────
@tree.command(name="set-title", description="Rename a set's display title (MP4 filename unchanged)")
@app_commands.describe(set="Set name (fuzzy)", title="New display title")
async def set_title(interaction: discord.Interaction, set: str, title: str):
    if not _guard(interaction):
        return

    idx, pick = resolve_set(set)
    if pick is None:
        await interaction.response.send_message(embed=err_embed("Set not found", f"No match for `{set}`."))
        return

    picks = load_json(STAFF_PICKS_FILE)
    old_title = picks[idx].get("title", picks[idx].get("filename", "?"))
    picks[idx]["title"] = title
    save_json(STAFF_PICKS_FILE, picks)

    await interaction.response.send_message(embed=ok_embed(
        "Title updated",
        f"`{old_title}` → `{title}`"
    ))


# ── /set-genre ─────────────────────────────────────────────────────────────
@tree.command(name="set-genre", description="Override genre tags for a set (comma-separated)")
@app_commands.describe(set="Set name (fuzzy)", genres="Genres, comma-separated (e.g. House, Techno)")
async def set_genre(interaction: discord.Interaction, set: str, genres: str):
    if not _guard(interaction):
        return

    idx, pick = resolve_set(set)
    if pick is None:
        await interaction.response.send_message(embed=err_embed("Set not found", f"No match for `{set}`."))
        return

    genre_list = [g.strip() for g in genres.split(",") if g.strip()]
    picks = load_json(STAFF_PICKS_FILE)
    picks[idx]["genres"] = genre_list
    save_json(STAFF_PICKS_FILE, picks)

    await interaction.response.send_message(embed=ok_embed(
        f"Genres updated — {pick.get('title', set)}",
        ", ".join(genre_list)
    ))


# ── /set-genre-reset ───────────────────────────────────────────────────────
@tree.command(name="set-genre-reset", description="Clear genre override — revert to auto-detected from tracklist")
@app_commands.describe(set="Set name (fuzzy)")
async def set_genre_reset(interaction: discord.Interaction, set: str):
    if not _guard(interaction):
        return

    idx, pick = resolve_set(set)
    if pick is None:
        await interaction.response.send_message(embed=err_embed("Set not found", f"No match for `{set}`."))
        return

    picks = load_json(STAFF_PICKS_FILE)
    picks[idx].pop("genres", None)
    save_json(STAFF_PICKS_FILE, picks)

    await interaction.response.send_message(embed=ok_embed(
        f"Genre override cleared — {pick.get('title', set)}",
        "Will now use auto-detected genres from tracklist."
    ))


# ── /set-order ─────────────────────────────────────────────────────────────
@tree.command(name="set-order", description="Move a set to position N in the staff picks list")
@app_commands.describe(set="Set name (fuzzy)", position="New 1-based position")
async def set_order(interaction: discord.Interaction, set: str, position: int):
    if not _guard(interaction):
        return

    idx, pick = resolve_set(set)
    if pick is None:
        await interaction.response.send_message(embed=err_embed("Set not found", f"No match for `{set}`."))
        return

    picks = load_json(STAFF_PICKS_FILE)
    entry = picks.pop(idx)
    new_idx = max(0, min(position - 1, len(picks)))
    picks.insert(new_idx, entry)
    save_json(STAFF_PICKS_FILE, picks)

    await interaction.response.send_message(embed=ok_embed(
        f"Moved — {entry.get('title', set)}",
        f"Now at position **{new_idx + 1}**."
    ))


# ── /set-remove ────────────────────────────────────────────────────────────
@tree.command(name="set-remove", description="Remove a set from staff picks")
@app_commands.describe(set="Set name (fuzzy)")
async def set_remove(interaction: discord.Interaction, set: str):
    if not _guard(interaction):
        return

    idx, pick = resolve_set(set)
    if pick is None:
        await interaction.response.send_message(embed=err_embed("Set not found", f"No match for `{set}`."))
        return

    picks = load_json(STAFF_PICKS_FILE)
    removed = picks.pop(idx)
    save_json(STAFF_PICKS_FILE, picks)

    await interaction.response.send_message(embed=ok_embed(
        "Set removed",
        f"`{removed.get('title', removed.get('filename', set))}` removed from staff picks.\n"
        f"_(MP4 file on disk is untouched)_"
    ))


# ── /set-add ───────────────────────────────────────────────────────────────
@tree.command(name="set-add", description="Add an existing MP4 to staff picks")
@app_commands.describe(filename="Exact MP4 filename (e.g. Ronaut[013]-Set.mp4)", title="Display title")
async def set_add(interaction: discord.Interaction, filename: str, title: str):
    if not _guard(interaction):
        return

    # Sanity check: file should exist on VPS (we can't verify locally, just trust the user)
    picks = load_json(STAFF_PICKS_FILE)

    # Prevent duplicates
    needle = _normalize(filename)
    for pick in picks:
        if _normalize(pick.get("filename", "")) == needle:
            await interaction.response.send_message(embed=err_embed(
                "Already exists",
                f"`{filename}` is already in staff picks as `{pick.get('title', '?')}`."
            ))
            return

    new_entry = {"filename": filename, "title": title, "description": ""}
    picks.append(new_entry)
    save_json(STAFF_PICKS_FILE, picks)

    await interaction.response.send_message(embed=ok_embed(
        "Set added",
        f"**{title}** (`{filename}`) added at position **{len(picks)}**."
    ))


# ── /tracklist ─────────────────────────────────────────────────────────────
@tree.command(name="tracklist", description="Show the tracklist for a set")
@app_commands.describe(set="Set name (fuzzy)")
async def tracklist_cmd(interaction: discord.Interaction, set: str):
    if not _guard(interaction):
        return

    tl_path = tracklist_path_for(set)
    if not tl_path:
        await interaction.response.send_message(embed=err_embed("No tracklist", f"No tracklist file found for `{set}`."))
        return

    try:
        data = load_json(tl_path)
    except (OSError, json.JSONDecodeError) as e:
        await interaction.response.send_message(embed=err_embed("Load error", str(e)))
        return

    tracks = data.get("tracklist", [])
    if not tracks:
        await interaction.response.send_message(embed=err_embed("Empty tracklist", "No tracks identified yet."))
        return

    # Build paginated output (Discord embed description cap: ~4096 chars)
    lines = []
    for i, t in enumerate(tracks, 1):
        ts = _fmt_time(t.get("start_time", 0))
        artists = ", ".join(t.get("artists", [])) or t.get("artist", "?")
        title_ = t.get("title", "?")
        lines.append(f"**{i}.** `{ts}` — {artists} – {title_}")

    description = "\n".join(lines)
    if len(description) > 3800:
        description = description[:3800] + f"\n… _(+{len(tracks) - lines[:50].__len__()} more)_"

    set_title_ = data.get("set_name", set)
    embed = discord.Embed(
        title=f"Tracklist — {set_title_}",
        description=description,
        color=0x3498DB
    )
    embed.set_footer(text=f"{len(tracks)} tracks identified")
    await interaction.response.send_message(embed=embed)


# ── /track-add ─────────────────────────────────────────────────────────────
@tree.command(name="track-add", description="Add a track to a set's tracklist at a given timestamp")
@app_commands.describe(
    set="Set name (fuzzy)",
    time="Timestamp (mm:ss or hh:mm:ss)",
    artist="Artist name(s)",
    title="Track title"
)
async def track_add(interaction: discord.Interaction, set: str, time: str, artist: str, title: str):
    if not _guard(interaction):
        return

    tl_path = tracklist_path_for(set)
    if not tl_path:
        await interaction.response.send_message(embed=err_embed("No tracklist", f"No tracklist file for `{set}`."))
        return

    try:
        start_sec = _parse_timestamp(time)
    except ValueError as e:
        await interaction.response.send_message(embed=err_embed("Bad timestamp", str(e)))
        return

    try:
        data = load_json(tl_path)
    except (OSError, json.JSONDecodeError) as e:
        await interaction.response.send_message(embed=err_embed("Load error", str(e)))
        return

    tracks = data.get("tracklist", [])
    new_track = {
        "start_time": start_sec,
        "artists": [a.strip() for a in artist.split(",")],
        "title": title,
        "manually_added": True,
        "added_at": int(_time.time()),
    }
    tracks.append(new_track)
    tracks.sort(key=lambda t: t.get("start_time", 0))
    data["tracklist"] = tracks
    save_json(tl_path, data)

    pos = next(i + 1 for i, t in enumerate(tracks) if t is new_track)
    await interaction.response.send_message(embed=ok_embed(
        "Track added",
        f"**{artist} – {title}** at `{_fmt_time(start_sec)}` (position {pos})"
    ))


# ── /track-remove ──────────────────────────────────────────────────────────
@tree.command(name="track-remove", description="Remove a track at position N from a set's tracklist")
@app_commands.describe(set="Set name (fuzzy)", position="1-based position from /tracklist output")
async def track_remove(interaction: discord.Interaction, set: str, position: int):
    if not _guard(interaction):
        return

    tl_path = tracklist_path_for(set)
    if not tl_path:
        await interaction.response.send_message(embed=err_embed("No tracklist", f"No tracklist file for `{set}`."))
        return

    try:
        data = load_json(tl_path)
    except (OSError, json.JSONDecodeError) as e:
        await interaction.response.send_message(embed=err_embed("Load error", str(e)))
        return

    tracks = data.get("tracklist", [])
    if position < 1 or position > len(tracks):
        await interaction.response.send_message(embed=err_embed(
            "Out of range",
            f"Position must be 1–{len(tracks)}."
        ))
        return

    removed = tracks.pop(position - 1)
    data["tracklist"] = tracks
    save_json(tl_path, data)

    artists = ", ".join(removed.get("artists", [])) or removed.get("artist", "?")
    await interaction.response.send_message(embed=ok_embed(
        "Track removed",
        f"Removed **{artists} – {removed.get('title', '?')}** (was position {position})"
    ))


# ── /track-edit ────────────────────────────────────────────────────────────
@tree.command(name="track-edit", description="Edit one field of a track in a set's tracklist")
@app_commands.describe(
    set="Set name (fuzzy)",
    position="1-based position from /tracklist output",
    field="Field to edit: title | artist | album",
    value="New value"
)
async def track_edit(interaction: discord.Interaction, set: str, position: int, field: str, value: str):
    if not _guard(interaction):
        return

    ALLOWED_FIELDS = {"title", "artist", "album"}
    if field.lower() not in ALLOWED_FIELDS:
        await interaction.response.send_message(embed=err_embed(
            "Invalid field",
            f"Field must be one of: {', '.join(sorted(ALLOWED_FIELDS))}"
        ))
        return

    tl_path = tracklist_path_for(set)
    if not tl_path:
        await interaction.response.send_message(embed=err_embed("No tracklist", f"No tracklist file for `{set}`."))
        return

    try:
        data = load_json(tl_path)
    except (OSError, json.JSONDecodeError) as e:
        await interaction.response.send_message(embed=err_embed("Load error", str(e)))
        return

    tracks = data.get("tracklist", [])
    if position < 1 or position > len(tracks):
        await interaction.response.send_message(embed=err_embed(
            "Out of range",
            f"Position must be 1–{len(tracks)}."
        ))
        return

    track = tracks[position - 1]
    field_lower = field.lower()

    if field_lower == "artist":
        old = ", ".join(track.get("artists", []))
        track["artists"] = [a.strip() for a in value.split(",")]
    elif field_lower == "title":
        old = track.get("title", "")
        track["title"] = value
    elif field_lower == "album":
        old = track.get("album", "")
        track["album"] = value

    data["tracklist"] = tracks
    save_json(tl_path, data)

    ts = _fmt_time(track.get("start_time", 0))
    await interaction.response.send_message(embed=ok_embed(
        "Track updated",
        f"Track at `{ts}` — **{field}**:\n`{old}` → `{value}`"
    ))


# ── Bot events ─────────────────────────────────────────────────────────────
@client.event
async def on_ready():
    await tree.sync()
    print(f"Ronaut bot ready — logged in as {client.user} (ID: {client.user.id})")
    print(f"Slash commands synced.")


# ── Entrypoint ─────────────────────────────────────────────────────────────
token = os.environ.get("DISCORD_BOT_TOKEN")
if not token:
    raise RuntimeError("DISCORD_BOT_TOKEN environment variable not set.")

client.run(token)
