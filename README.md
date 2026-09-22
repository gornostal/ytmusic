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
uv run ytm.py songs -p <playlistId> # list songs in a playlist
uv run ytm.py artists -p <playlistId> [--sort count]
```

`-p LM` is the "Liked songs" playlist. Every command takes `--json` for raw output.

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
