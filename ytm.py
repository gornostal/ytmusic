#!/usr/bin/env python3
"""Small CLI over ytmusicapi: list playlists, their songs, and their artists."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from rich.console import Console
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
        table.add_row(p["playlistId"], p["title"], str(p.get("count") or ""))
    console.print(table)


def cmd_songs(args: argparse.Namespace) -> None:
    yt = load_client()
    pl = yt.get_playlist(args.playlist, limit=None)
    tracks = pl.get("tracks") or []

    if args.json:
        print(json.dumps(tracks, indent=2))
        return

    table = Table(title=f"{pl.get('title', args.playlist)} — {len(tracks)} songs", header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Title")
    table.add_column("Artists", style="magenta")
    table.add_column("Album", style="dim")
    table.add_column("Length", justify="right", style="dim")
    for i, t in enumerate(tracks, 1):
        album = (t.get("album") or {}).get("name", "") if t.get("album") else ""
        table.add_row(str(i), t.get("title", ""), ", ".join(artists_of(t)), album, t.get("duration") or "")
    console.print(table)


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

    table = Table(title=f"{pl.get('title', args.playlist)} — {len(rows)} artists", header_style="bold")
    table.add_column("Artist", style="magenta")
    table.add_column("Songs", justify="right", style="dim")
    for name, count in rows:
        table.add_row(name, str(count))
    console.print(table)


def main() -> None:
    parser = argparse.ArgumentParser(prog="ytm", description="Browse your YouTube Music library.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, help_, fn):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--json", action="store_true", help="output raw JSON instead of a table")
        p.set_defaults(func=fn)
        return p

    add("login", "store browser credentials", cmd_login)

    p = add("playlists", "list your library playlists", cmd_playlists)
    p.add_argument("-n", "--limit", type=int, default=None, help="max playlists (default: all)")

    for name, help_, fn in [
        ("songs", "list songs in a playlist", cmd_songs),
        ("artists", "list distinct artists in a playlist", cmd_artists),
    ]:
        p = add(name, help_, fn)
        p.add_argument("-p", "--playlist", required=True, metavar="ID", help="playlist ID ('LM' = liked songs)")
        if name == "artists":
            p.add_argument("--sort", choices=["name", "count"], default="name", help="sort order (default: name)")

    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
