# ytm

A small CLI over [ytmusicapi](https://ytmusicapi.readthedocs.io) for browsing your YouTube Music library.

## Setup

```sh
uv sync
```

## Usage

```sh
uv run ytm.py login                 # paste a copied request once
uv run ytm.py playlists             # list playlists (with their IDs)
uv run ytm.py liked [-n 50]         # list liked songs
uv run ytm.py songs -p <playlistId> # list songs in a playlist
uv run ytm.py artists -p <playlistId> [--sort count]
uv run ytm.py artist "a-ha" [--all] # artist info, top songs, albums, singles
```

Editing:

```sh
uv run ytm.py create-playlist "Title" [-d "description"] [--privacy PUBLIC]
uv run ytm.py add-songs -p <playlistId> <videoId>...
uv run ytm.py remove-songs -p <playlistId> <videoId>...   # asks y/N
uv run ytm.py delete-playlist -p <playlistId>             # asks y/N
```

`-p LM` is the "Liked songs" playlist. Every command takes `--json` for raw
output, and the `songs`/`liked` tables print the video IDs the editing commands
take.

The two destructive commands ask for confirmation, defaulting to no; `-y` skips
the prompt. `remove-songs` needs a song to actually be in the playlist (it looks
up the internal per-item ID), and it reports any IDs it could not find.

### Duplicates

`add-songs` does not skip duplicates — YouTube rejects the whole call if any of
the songs is already in the playlist, so nothing is added, not even the new ones.
The command reports that and exits 1. `--duplicates` adds them a second time.

### Liked songs

`-p LM` works for `add-songs` and `remove-songs` too, but liking is not a
playlist edit, so those become like/unlike calls on each song. YouTube caches the
liked list for a while — a song can still show up in `liked` right after you
remove it.

### artist

Takes either a `UC...` channel ID or a name to search for; with a name it uses
the top artist result and prints which one it matched on stderr. The artist page
only highlights ~10 albums and singles, `--all` fetches the full lists.

### login

Chrome no longer has "Copy request headers". In DevTools -> Network, right-click
a POST to `/youtubei/v1/` and use **Copy -> Copy as cURL**; paste that into
`login` and press Ctrl-D. Raw header text and "Copy as fetch (Node.js)" are also
accepted. Plain "Copy as fetch" is not — it omits cookies, and `login` says so.

`login` verifies the paste against the account endpoint and prints your account
name, because bad cookies otherwise just return an empty library.

Credentials are stored in `~/.config/ytm/browser.json` (mode 600). They're the
browser cookies for your Google account — they expire after a while, just run
`login` again.
