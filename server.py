"""Server MCP minimale per i file di un server Nitrado (es. DayZ).

Strumenti esposti: list_files, read_file, write_file.
- write_file fa SEMPRE un backup del file esistente prima di sovrascriverlo.
- Si possono toccare solo file con estensioni consentite e sotto ALLOWED_ROOT.
- Il server risponde solo sull'indirizzo che contiene MCP_SECRET.

Variabili d'ambiente richieste (mai nel codice!):
  NITRADO_TOKEN       token API creato dal pannello Nitrado
  NITRADO_SERVICE_ID  ID numerico del servizio
  MCP_SECRET          stringa lunga e casuale, fa parte dell'URL
Opzionali:
  ALLOWED_ROOT        cartella consentita (default: /games)
  PORT                porta (default: 8000)
"""
import os
import posixpath
import time

import httpx
from mcp.server.fastmcp import FastMCP

TOKEN = os.environ["NITRADO_TOKEN"]
SERVICE_ID = os.environ["NITRADO_SERVICE_ID"]
SECRET = os.environ["MCP_SECRET"]
ALLOWED_ROOT = os.environ.get("ALLOWED_ROOT", "/games").rstrip("/")
API = "https://api.nitrado.net"
ALLOWED_EXT = (".xml", ".json", ".c", ".txt", ".cfg")
MAX_BYTES = 1_000_000

mcp = FastMCP(
    "nitrado-dayz",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8000")),
    stateless_http=True,
    streamable_http_path=f"/{SECRET}/mcp",
)

HEADERS = {"Authorization": f"Bearer {TOKEN}"}
BASE = f"{API}/services/{SERVICE_ID}/gameservers/file_server"


def _check_path(path: str, must_have_ext: bool = True) -> str:
    clean = posixpath.normpath(path)
    if not (clean == ALLOWED_ROOT or clean.startswith(ALLOWED_ROOT + "/")):
        raise ValueError(f"Percorso fuori dalla cartella consentita ({ALLOWED_ROOT}).")
    if must_have_ext and not clean.lower().endswith(ALLOWED_EXT):
        raise ValueError(f"Estensione non consentita. Ammesse: {ALLOWED_EXT}")
    return clean


async def _download(client: httpx.AsyncClient, path: str) -> str:
    r = await client.get(f"{BASE}/download", params={"file": path}, headers=HEADERS)
    r.raise_for_status()
    url = r.json()["data"]["token"]["url"]
    f = await client.get(url)
    f.raise_for_status()
    if len(f.content) > MAX_BYTES:
        raise ValueError("File troppo grande.")
    return f.text


async def _upload(client: httpx.AsyncClient, path: str, content: str) -> None:
    directory, name = posixpath.split(path)
    r = await client.post(
        f"{BASE}/upload", params={"path": directory, "file": name}, headers=HEADERS
    )
    r.raise_for_status()
    tok = r.json()["data"]["token"]
    up = await client.post(
        tok["url"],
        headers={"token": tok["token"], "Content-Type": "application/octet-stream"},
        content=content.encode("utf-8"),
    )
    up.raise_for_status()


@mcp.tool()
async def server_info() -> str:
    """Mostra utente e percorso base del server Nitrado (serve per trovare i
    percorsi assoluti dei file). Non mostra password né token."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{API}/services/{SERVICE_ID}/gameservers", headers=HEADERS)
        r.raise_for_status()
        gs = r.json()["data"]["gameserver"]
    gsp = gs.get("game_specific") or {}
    return (
        f"username: {gs.get('username')}\n"
        f"path: {gsp.get('path')}\n"
        f"game: {gs.get('game')}\n"
        f"status: {gs.get('status')}"
    )


@mcp.tool()
async def list_files(directory: str = ALLOWED_ROOT) -> str:
    """Elenca file e cartelle in una cartella del server Nitrado."""
    directory = _check_path(directory, must_have_ext=False)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{BASE}/list", params={"dir": directory}, headers=HEADERS)
        r.raise_for_status()
        entries = r.json()["data"]["entries"]
    return "\n".join(f"{e.get('type', '?'):5} {e.get('size', '')!s:>10}  {e['path']}" for e in entries)


@mcp.tool()
async def read_file(path: str) -> str:
    """Legge un file di testo (xml, json, c, txt, cfg) dal server Nitrado."""
    path = _check_path(path)
    async with httpx.AsyncClient(timeout=60) as client:
        return await _download(client, path)


@mcp.tool()
async def write_file(path: str, content: str) -> str:
    """Sovrascrive un file sul server Nitrado, dopo aver salvato un backup
    dell'originale nella stessa cartella (nome.bak-AAAAMMGG-HHMMSS)."""
    path = _check_path(path)
    if len(content.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Contenuto troppo grande.")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    async with httpx.AsyncClient(timeout=60) as client:
        backup_note = "nessun file precedente, nessun backup creato"
        try:
            old = await _download(client, path)
            await _upload(client, f"{path}.bak-{stamp}", old)
            backup_note = f"backup salvato come {path}.bak-{stamp}"
        except httpx.HTTPStatusError:
            pass  # il file non esisteva ancora
        await _upload(client, path, content)
    return f"Scritto {path} ({backup_note})."


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
