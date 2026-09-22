#!/usr/bin/env python3
"""Small CLI over ytmusicapi: browse playlists, songs and artists, and edit playlists."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.table import Table
from ytmusicapi import YTMusic, setup

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ytm"
AUTH_FILE = CONFIG_DIR / "browser.json"

console = Console()
err = Console(stderr=True)

LOGIN_HELP = """\
1. Open https://music.youtube.com in your browser, logged in.
2. Open DevTools -> Network, filter for a POST request to /youtubei/v1/ (click
   around the site if the list is empty).
3. Right-click that request -> Copy -> [bold]Copy as cURL[/]. (Raw request headers
   or 'Copy as fetch (Node.js)' work too; plain 'Copy as fetch' does not, it
   leaves out your cookies.)
4. Paste below, then press Ctrl-D on an empty line.
"""


_CURL_FLAG = re.compile(r"(?:^|\s)(-H|--header|-b|--cookie)\s+")


def _read_shell_arg(text: str, i: int) -> tuple[str, int]:
    """Read one shell-quoted argument at index i; returns (value, index after it)."""
    ansi_c = text[i] == "$" and i + 1 < len(text) and text[i + 1] in "'\""
    if ansi_c:
        i += 1
    if text[i] in "'\"":
        quote, i, out = text[i], i + 1, []
        while i < len(text):
            if text[i] == "\\" and i + 1 < len(text) and (ansi_c or quote == '"'):
                out.append(text[i + 1])  # \' and \" and \\ lose their backslash
                i += 2
            elif text[i] == quote:
                return "".join(out), i + 1
            else:
                out.append(text[i])
                i += 1
        return "".join(out), i
    end = i
    while end < len(text) and not text[end].isspace():
        end += 1
    return text[i:end], end


def _headers_from_curl(text: str) -> dict[str, str]:
    """Pull header/cookie flags out of a 'Copy as cURL' command."""
    headers: dict[str, str] = {}
    for match in _CURL_FLAG.finditer(text):
        value, _ = _read_shell_arg(text, match.end())
        if match.group(1) in ("-b", "--cookie"):
            headers["cookie"] = value
        else:
            key, _, val = value.partition(":")
            headers[key.strip().lower()] = val.strip()
    return headers


def _headers_from_fetch(text: str) -> dict[str, str]:
    """Pull the headers object out of a 'Copy as fetch' snippet."""
    match = re.search(r'"headers"\s*:\s*\{', text)
    if not match:
        return {}
    start = match.end() - 1
    depth = 0
    for i in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            block = text[start : i + 1]
            break
    else:
        return {}
    try:
        return {k.lower(): v for k, v in json.loads(block).items()}
    except json.JSONDecodeError:
        return {}


def normalize_headers(pasted: str) -> str:
    """Turn a cURL / fetch / raw-headers paste into the 'key: value' lines ytmusicapi parses."""
    headers = _headers_from_fetch(pasted) if "fetch(" in pasted else {}
    if not headers and "curl" in pasted[:200]:
        headers = _headers_from_curl(pasted)
    if not headers:
        return pasted  # already raw headers; let ytmusicapi parse it

    headers.pop("content-length", None)
    if "cookie" in headers and "authorization" not in headers:
        # recomputed from the cookie on every request anyway, but its presence
        # is what marks the saved file as browser auth
        headers["authorization"] = "SAPISIDHASH"
    return "\n".join(f"{k}: {v}" for k, v in headers.items())


def load_client() -> YTMusic:
    if not AUTH_FILE.exists():
        err.print(f"[red]Not logged in.[/] Run [bold]ytm login[/] first (no credentials at {AUTH_FILE}).")
        sys.exit(1)
    try:
        return YTMusic(str(AUTH_FILE))
    except Exception as exc:  # malformed/expired headers
        err.print(f"[red]Could not authenticate:[/] {exc}\nTry [bold]ytm login[/] again.")
        sys.exit(1)


def artists_of(track: dict) -> list[str]:
    return [a["name"] for a in (track.get("artists") or []) if a.get("name")]


def cmd_login(args: argparse.Namespace) -> None:
    console.print(LOGIN_HELP)
    console.print("[dim]Paste request headers:[/]")
    pasted = sys.stdin.read().strip()
    if not pasted:
        err.print("[red]Nothing pasted, aborting.[/]")
        sys.exit(1)
    headers_raw = normalize_headers(pasted)
    if "cookie:" not in headers_raw.lower():
        err.print(
            "[red]No cookie header in that paste.[/] 'Copy as fetch' strips cookies — "
            "use [bold]Copy as cURL[/] instead."
        )
        sys.exit(1)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        setup(filepath=str(AUTH_FILE), headers_raw=headers_raw)
    except Exception as exc:
        err.print(f"[red]Those headers were not accepted:[/] {exc}")
        sys.exit(1)
    AUTH_FILE.chmod(0o600)

    # an invalid cookie still returns an empty library rather than an error,
    # so verify against the account endpoint instead
    try:
        account = YTMusic(str(AUTH_FILE)).get_account_info()
    except Exception:
        AUTH_FILE.unlink(missing_ok=True)  # don't leave creds that silently return empty results
        err.print(
            "[red]Saved, but YouTube did not recognize those credentials.[/] Make sure you were "
            "signed in and copied a POST request to /youtubei/v1/, then try again."
        )
        sys.exit(1)
    console.print(f"[green]Logged in as {account.get('accountName', 'unknown')}.[/] Saved to {AUTH_FILE}")


def cmd_playlists(args: argparse.Namespace) -> None:
    yt = load_client()
    playlists = yt.get_library_playlists(limit=args.limit)

    if args.json:
        print(json.dumps(playlists, indent=2))
        return

    table = Table(title=f"Playlists ({len(playlists)})", header_style="bold")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title")
    table.add_column("Tracks", justify="right", style="dim")
    for p in playlists:
        table.add_row(p["playlistId"], escape(p["title"]), str(p.get("count") or ""))
    console.print(table)


def confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    prompt = f"{question} \\[y/N] "
    if sys.stdin.isatty():
        answer = console.input(prompt)
    else:
        console.print(prompt, end="")
        answer = sys.stdin.readline()  # EOF reads as "", i.e. declined
    return answer.strip().lower() in ("y", "yes")


def api(action: str, fn, *fn_args, **kwargs):
    """Run a write call, turning ytmusicapi's exceptions into a one-line error."""
    try:
        return fn(*fn_args, **kwargs)
    except Exception as exc:
        err.print(f"[red]Could not {action}:[/] {exc}")
        sys.exit(1)


def response_message(result: dict) -> str | None:
    """The text YouTube would have shown in a toast or dialog, if the edit was refused."""
    def walk(node):
        if isinstance(node, dict):
            # prefer the message over the dialog title, which walk would reach first
            for key in ("responseText", "dialogMessages"):
                if key in node and (found := walk(node[key])):
                    return found
            runs = node.get("runs")
            if isinstance(runs, list):
                text = "".join(r.get("text", "") for r in runs if isinstance(r, dict))
                if text:
                    return text
            return next((found for v in node.values() if (found := walk(v))), None)
        if isinstance(node, list):
            return next((found for v in node if (found := walk(v))), None)
        return None

    return walk(result.get("actions")) if isinstance(result, dict) else None


def print_tracks(title: str, tracks: list[dict]) -> None:
    table = Table(title=title, header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Title")
    table.add_column("Artists", style="magenta")
    table.add_column("Album", style="dim")
    table.add_column("Length", justify="right", style="dim")
    table.add_column("ID", style="cyan", no_wrap=True)
    for i, t in enumerate(tracks, 1):
        album = (t.get("album") or {}).get("name", "") if t.get("album") else ""
        row = [escape(t.get("title", "")), escape(", ".join(artists_of(t))), escape(album)]
        table.add_row(str(i), *row, t.get("duration") or "", t.get("videoId") or "")
    console.print(table)


def cmd_songs(args: argparse.Namespace) -> None:
    yt = load_client()
    pl = yt.get_playlist(args.playlist, limit=None)
    tracks = pl.get("tracks") or []

    if args.json:
        print(json.dumps(tracks, indent=2))
        return
    print_tracks(f"{escape(pl.get('title', args.playlist))} — {len(tracks)} songs", tracks)


def cmd_liked(args: argparse.Namespace) -> None:
    yt = load_client()
    pl = yt.get_liked_songs(limit=args.limit)
    # the API rounds up to whole pages, so cut it back to what was asked for
    tracks = (pl.get("tracks") or [])[: args.limit]

    if args.json:
        print(json.dumps(tracks, indent=2))
        return
    total = pl.get("trackCount") or len(tracks)
    shown = f"{len(tracks)} of {total}" if len(tracks) < total else f"{len(tracks)}"
    print_tracks(f"Liked songs — {shown}", tracks)


def cmd_artists(args: argparse.Namespace) -> None:
    yt = load_client()
    pl = yt.get_playlist(args.playlist, limit=None)
    tracks = pl.get("tracks") or []

    counts: dict[str, int] = {}
    for t in tracks:
        for name in artists_of(t):
            counts[name] = counts.get(name, 0) + 1

    if args.sort == "count":
        rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    else:
        rows = sorted(counts.items(), key=lambda kv: kv[0].lower())

    if args.json:
        print(json.dumps([{"artist": n, "songs": c} for n, c in rows], indent=2))
        return

    table = Table(title=f"{escape(pl.get('title', args.playlist))} — {len(rows)} artists", header_style="bold")
    table.add_column("Artist", style="magenta")
    table.add_column("Songs", justify="right", style="dim")
    for name, count in rows:
        table.add_row(escape(name), str(count))
    console.print(table)


_CHANNEL_ID = re.compile(r"UC[\w-]{20,}")


def resolve_artist(yt: YTMusic, query: str) -> str:
    """Accept a channel ID as-is; otherwise take the top artist search result."""
    if _CHANNEL_ID.fullmatch(query):
        return query
    results = yt.search(query, filter="artists", limit=5)
    if not results:
        err.print(f"[red]No artist found for[/] {query!r}.")
        sys.exit(1)
    top = results[0]
    err.print(f"[dim]Matched artist {escape(top['artist'])} ({top['browseId']})[/]")
    return top["browseId"]


def all_releases(yt: YTMusic, section: dict | None, expand: bool) -> list[dict]:
    """The releases in an artist section — the ~10 shown, or every one of them."""
    results = (section or {}).get("results") or []
    params, browse_id = (section or {}).get("params"), (section or {}).get("browseId")
    if not (expand and params and browse_id):
        return results  # nothing more to fetch: the section is already complete
    try:
        return yt.get_artist_albums(browse_id, params, limit=None)
    except Exception as exc:
        err.print(f"[yellow]Could not load the full list, showing the first {len(results)}:[/] {exc}")
        return results


def print_releases(title: str, releases: list[dict]) -> None:
    if not releases:
        return
    table = Table(title=f"{title} ({len(releases)})", header_style="bold")
    table.add_column("Title")
    table.add_column("Type", style="dim")
    table.add_column("Year", justify="right", style="dim")
    table.add_column("ID", style="cyan", no_wrap=True)
    for r in releases:
        table.add_row(escape(r.get("title", "")), r.get("type") or "Album", str(r.get("year") or ""), r.get("browseId") or "")
    console.print(table)


def cmd_artist(args: argparse.Namespace) -> None:
    yt = load_client()
    channel_id = resolve_artist(yt, args.artist)
    try:
        info = yt.get_artist(channel_id)
    except Exception as exc:
        err.print(f"[red]Could not load that artist:[/] {exc}")
        sys.exit(1)

    albums = all_releases(yt, info.get("albums"), args.all)
    singles = all_releases(yt, info.get("singles"), args.all)

    if args.json:
        print(json.dumps({**info, "albums": albums, "singles": singles}, indent=2))
        return

    facts = [f"{info[k]}" for k in ("subscribers", "monthlyListeners", "views") if info.get(k)]
    labels = ["subscribers", "monthly listeners", ""]
    summary = ", ".join(f"{v} {lab}".strip() for v, lab in zip(facts, labels))
    console.print(f"\n[bold magenta]{escape(info.get('name', channel_id))}[/] [cyan]{channel_id}[/]")
    if summary:
        console.print(f"[dim]{summary}[/]")
    if info.get("description"):
        console.print(f"\n{escape(info['description'])}\n")

    songs = (info.get("songs") or {}).get("results") or []
    if songs:
        print_tracks("Top songs", songs)
    print_releases("Albums", albums)
    print_releases("Singles", singles)
    if not args.all and (albums or singles):
        console.print("[dim]Showing the highlighted releases; pass --all for every one.[/]")


def cmd_create_playlist(args: argparse.Namespace) -> None:
    yt = load_client()
    result = api(
        "create the playlist",
        yt.create_playlist,
        args.title,
        args.description or "",
        privacy_status=args.privacy,
    )
    if not isinstance(result, str):  # a dict here is an error response, not an ID
        err.print(f"[red]Could not create the playlist:[/] {json.dumps(result)[:300]}")
        sys.exit(1)

    if args.json:
        print(json.dumps({"playlistId": result, "title": args.title}, indent=2))
        return
    console.print(f"[green]Created[/] {escape(args.title)} — [cyan]{result}[/]")


def cmd_delete_playlist(args: argparse.Namespace) -> None:
    yt = load_client()
    try:
        pl = yt.get_playlist(args.playlist, limit=1)
        label = escape(f"{pl.get('title', args.playlist)} ({pl.get('trackCount', '?')} songs)")
    except Exception:
        label = args.playlist  # unreadable, but it may still be deletable
    if not confirm(f"Delete playlist [bold]{label}[/]?", args.yes):
        console.print("[dim]Left alone.[/]")
        return

    api("delete that playlist", yt.delete_playlist, args.playlist)
    console.print(f"[green]Deleted[/] {label}")


def cmd_add_songs(args: argparse.Namespace) -> None:
    yt = load_client()
    if args.playlist == "LM":  # liked songs is not editable as a playlist
        for video_id in args.video_ids:
            api(f"like {video_id}", yt.rate_song, video_id, "LIKE")
        console.print(f"[green]Liked[/] {len(args.video_ids)} song(s).")
        return

    result = api(
        "add those songs",
        yt.add_playlist_items,
        args.playlist,
        args.video_ids,
        duplicates=args.duplicates,
    )
    if args.json:
        print(json.dumps(result, indent=2))

    # a rejected edit comes back as a normal response with a failed status,
    # and it is all-or-nothing: not even the new songs get added
    status = result.get("status", "") if isinstance(result, dict) else str(result)
    if "SUCCEEDED" not in status:
        reason = response_message(result) or status or "unknown error"
        err.print(f"[red]Nothing was added:[/] {reason}")
        if "duplicat" in reason.lower() or "already in" in reason.lower():
            err.print("[dim]Pass --duplicates to add them anyway.[/]")
        sys.exit(1)

    if not args.json:
        console.print(f"[green]Added[/] {len(args.video_ids)} song(s) to [cyan]{args.playlist}[/]")


def cmd_remove_songs(args: argparse.Namespace) -> None:
    yt = load_client()
    wanted = dict.fromkeys(args.video_ids)  # de-duplicated, order kept

    if args.playlist == "LM":  # removing a like, not a playlist item
        if not confirm(f"Unlike {len(wanted)} song(s)?", args.yes):
            console.print("[dim]Left alone.[/]")
            return
        for video_id in wanted:
            api(f"unlike {video_id}", yt.rate_song, video_id, "INDIFFERENT")
        console.print(f"[green]Unliked[/] {len(wanted)} song(s).")
        return

    pl = yt.get_playlist(args.playlist, limit=None)
    items = [t for t in (pl.get("tracks") or []) if t.get("videoId") in wanted and t.get("setVideoId")]
    missing = [v for v in wanted if v not in {t.get("videoId") for t in items}]
    if missing:
        err.print(f"[yellow]Not in this playlist (or not removable):[/] {', '.join(missing)}")
    if not items:
        err.print("[red]Nothing to remove.[/]")
        sys.exit(1)

    listing = escape(", ".join(t.get("title", t["videoId"]) for t in items))
    title = escape(pl.get("title", args.playlist))
    if not confirm(f"Remove {len(items)} song(s) from [bold]{title}[/] ({listing})?", args.yes):
        console.print("[dim]Left alone.[/]")
        return

    api("remove those songs", yt.remove_playlist_items, args.playlist, items)
    console.print(f"[green]Removed[/] {len(items)} song(s) from {title}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="ytm", description="Browse and edit your YouTube Music library.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, help_, fn, **kwargs):
        p = sub.add_parser(name, help=help_, **kwargs)
        p.add_argument("--json", action="store_true", help="output raw JSON instead of a table")
        p.set_defaults(func=fn)
        return p

    def playlist_arg(p, help_="playlist ID ('LM' = liked songs)"):
        p.add_argument("-p", "--playlist", required=True, metavar="ID", help=help_)

    add("login", "store browser credentials", cmd_login)

    p = add("playlists", "list your library playlists", cmd_playlists)
    p.add_argument("-n", "--limit", type=int, default=None, help="max playlists (default: all)")

    p = add("liked", "list your liked songs", cmd_liked)
    p.add_argument("-n", "--limit", type=int, default=None, help="max songs (default: all)")

    playlist_arg(add("songs", "list songs in a playlist", cmd_songs))

    p = add("artists", "list distinct artists in a playlist", cmd_artists)
    playlist_arg(p)
    p.add_argument("--sort", choices=["name", "count"], default="name", help="sort order (default: name)")

    p = add("artist", "show an artist's details and releases", cmd_artist)
    p.add_argument("artist", metavar="NAME_OR_ID", help="artist name to search for, or a UC... channel ID")
    p.add_argument("--all", action="store_true", help="list every album and single, not just the highlights")

    p = add("create-playlist", "create an empty playlist", cmd_create_playlist)
    p.add_argument("title", help="playlist title")
    p.add_argument("-d", "--description", default="", help="playlist description")
    p.add_argument(
        "--privacy",
        choices=["PRIVATE", "PUBLIC", "UNLISTED"],
        default="PRIVATE",
        type=str.upper,
        help="who can see it (default: PRIVATE)",
    )

    p = add("delete-playlist", "delete one of your playlists", cmd_delete_playlist, aliases=["rm-playlist"])
    playlist_arg(p, "playlist ID to delete")
    p.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")

    p = add("add-songs", "add songs to a playlist", cmd_add_songs)
    playlist_arg(p)
    p.add_argument("video_ids", nargs="+", metavar="VIDEO_ID", help="song IDs (the ID column of 'songs')")
    p.add_argument("--duplicates", action="store_true", help="add songs already in the playlist again")

    p = add("remove-songs", "remove songs from a playlist", cmd_remove_songs, aliases=["rm-songs"])
    playlist_arg(p)
    p.add_argument("video_ids", nargs="+", metavar="VIDEO_ID", help="song IDs (the ID column of 'songs')")
    p.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")

    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
