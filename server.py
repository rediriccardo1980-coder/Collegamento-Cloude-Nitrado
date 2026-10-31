"""
Connettore MCP per i server Nitrado (DayZ console) tramite FTP.

- Supporta fino a 9 server, configurati con variabili d'ambiente (S1_*, S2_*, ...).
- Ogni modifica crea prima una copia di backup accanto al file originale.
- Le credenziali NON stanno nel codice: solo variabili d'ambiente su Render.
- READ_ONLY=true (default) blocca ogni scrittura. Mettilo a false per abilitarla.
- Include log di diagnostica dettagliati per analizzare errori di timeout e connessione.
"""

import base64
import hashlib
import io
import json
import os
import posixpath
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from ftplib import FTP, all_errors

from mcp.server.fastmcp import FastMCP

READ_ONLY = os.environ.get("READ_ONLY", "true").strip().lower() != "false"
SECRET = (os.environ.get("SECRET_PATH") or os.environ.get("MCP_SECRET") or "").strip().strip("/")
PORT = int(os.environ.get("PORT", "10000"))
MAX_CHARS = 100_000  # caratteri massimi restituiti per ogni lettura
TIMEOUT = int(os.environ.get("FTP_TIMEOUT", "12"))  # secondi

if not SECRET:
    raise SystemExit("Imposta SECRET_PATH oppure MCP_SECRET (stringa lunga e casuale).")

# Backup su GitHub (facoltativo): token "fine-grained" limitato al solo repository di backup.
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "").strip()          # es. utente/califfato-server-config
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip()
BACKUP_EXTS = (".xml", ".json", ".txt", ".cfg")
BACKUP_MAX_BYTES = int(os.environ.get("BACKUP_MAX_BYTES", str(3 * 1024 * 1024)))
BACKUP_SKIP = (".bak-", ".dst-backup", ".dst-rotator-backup", ".deleted-")
BACKUP_SKIP_DIRS = ("killfeed-pro-backups",)


def log(msg: str):
    """Stampa immediatamente nei log di Render."""
    print(f"[FTP-DEBUG] {msg}", file=sys.stdout, flush=True)


def load_servers():
    servers = {}
    for i in range(1, 10):
        host = os.environ.get(f"S{i}_HOST")
        if not host:
            continue
        name = os.environ.get(f"S{i}_NAME", f"server{i}").strip().lower()
        servers[name] = {
            "host": host.strip(),
            "port": int(os.environ.get(f"S{i}_PORT", "21")),
            "user": os.environ.get(f"S{i}_USER", "").strip(),
            "password": os.environ.get(f"S{i}_PASS", "").strip(),
        }
    return servers


SERVERS = load_servers()

mcp = FastMCP(
    "nitrado-ftp",
    host="0.0.0.0",
    port=PORT,
    streamable_http_path=f"/{SECRET}/mcp",
    stateless_http=True,
)


# ---------- utilità ----------

def _clean(path: str) -> str:
    if ".." in path.replace("\\", "/").split("/"):
        raise ValueError("Percorso non valido: '..' non è ammesso.")
    return posixpath.normpath("/" + path.strip())


def _ts() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _require_write():
    if READ_ONLY:
        raise PermissionError(
            "Il connettore è in sola lettura (READ_ONLY=true). "
            "Per abilitare le modifiche imposta READ_ONLY=false su Render."
        )


@contextmanager
def _ftp(server: str):
    cfg = SERVERS.get(server.strip().lower())
    if not cfg:
        raise ValueError(f"Server sconosciuto: {server}. Disponibili: {', '.join(SERVERS) or 'nessuno'}")
    
    log(f"Tentativo di connessione a {server} -> Host: '{cfg['host']}', Porta: {cfg['port']}, User: '{cfg['user']}'")
    ftp = FTP()
    ftp.encoding = "utf-8"
    try:
        t0 = time.time()
        ftp.connect(cfg["host"], cfg["port"], timeout=TIMEOUT)
        log(f"Connesso a {cfg['host']}:{cfg['port']} in {time.time() - t0:.2f}s")
        
        t1 = time.time()
        ftp.login(cfg["user"], cfg["password"])
        log(f"Login riuscito per {cfg['user']} in {time.time() - t1:.2f}s")
        
        ftp.set_pasv(True)
        log("Modalità PASV attivata.")
        yield ftp
    except Exception as e:
        log(f"ERRORE FTP su {server} ({cfg['host']}): {type(e).__name__}: {e}")
        raise
    finally:
        try:
            ftp.quit()
            log("Connessione FTP chiusa regolarmente.")
        except all_errors:
            ftp.close()
            log("Connessione FTP forzata alla chiusura.")


def _download(ftp: FTP, path: str) -> bytes:
    buf = io.BytesIO()
    ftp.retrbinary(f"RETR {path}", buf.write)
    return buf.getvalue()


def _upload(ftp: FTP, path: str, data: bytes) -> None:
    ftp.storbinary(f"STOR {path}", io.BytesIO(data))


def _exists(ftp: FTP, path: str) -> bool:
    try:
        ftp.size(path)
        return True
    except all_errors:
        return False


def _safe_write(ftp: FTP, path: str, data: bytes) -> str:
    """Scrive il file creando prima un backup. Restituisce il percorso del backup (o '')."""
    backup = ""
    if _exists(ftp, path):
        backup = f"{path}.bak-{_ts()}"
        _upload(ftp, backup, _download(ftp, path))
    _upload(ftp, path, data)
    if _download(ftp, path) != data:
        raise IOError("Verifica fallita: il file sul server non coincide con quello inviato.")
    return backup


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError("Il file non è testo UTF-8 (forse è binario).") from e


# ---------- strumenti di lettura ----------

@mcp.tool()
def list_servers() -> str:
    """Elenca i server Nitrado configurati e se il connettore è in sola lettura."""
    log(f"Esecuzione list_servers. Server registrati: {list(SERVERS.keys())}")
    if not SERVERS:
        return "Nessun server configurato (controlla le variabili S1_HOST, S1_USER, ...)."
    mode = "SOLA LETTURA" if READ_ONLY else "lettura e scrittura"
    return f"Modalità: {mode}\nServer: " + ", ".join(SERVERS)


@mcp.tool()
def test_connection(server: str) -> str:
    """Prova la connessione FTP passo per passo (connessione, login, cartella, elenco) per capire dove si blocca."""
    cfg = SERVERS.get(server.strip().lower())
    if not cfg:
        return f"Server sconosciuto: {server}. Disponibili: {', '.join(SERVERS) or 'nessuno'}"
    steps = []
    log(f"[TEST] Inizio test diagnostico su server '{server}' ({cfg['host']}:{cfg['port']})")
    ftp = FTP()
    ftp.encoding = "utf-8"
    try:
        t = time.time()
        steps.append(f"0. Parametri: Host='{cfg['host']}', Porta={cfg['port']}, User='{cfg['user']}'")
        ftp.connect(cfg["host"], cfg["port"], timeout=TIMEOUT)
        steps.append(f"1. Connessione TCP a {cfg['host']}:{cfg['port']} OK ({time.time() - t:.1f}s)")
        log(steps[-1])
        
        t = time.time()
        ftp.login(cfg["user"], cfg["password"])
        steps.append(f"2. Login OK ({time.time() - t:.1f}s)")
        log(steps[-1])
        
        steps.append(f"3. Cartella corrente: {ftp.pwd()}")
        log(steps[-1])
        
        ftp.set_pasv(True)
        lines = []
        t = time.time()
        ftp.retrlines("LIST", lines.append)
        steps.append(f"4. Elenco directory PASV OK ({len(lines)} voci in {time.time() - t:.1f}s)")
        log(steps[-1])
    except Exception as e:
        err_msg = f"ERRORE al passo {len(steps)}: {type(e).__name__}: {e}"
        steps.append(err_msg)
        log(f"[TEST FAIL] {err_msg}")
    finally:
        try:
            ftp.close()
        except Exception:
            pass
    return "\n".join(steps)


@mcp.tool()
def list_files(server: str, path: str = "/") -> str:
    """Elenca file e cartelle di un percorso FTP del server indicato."""
    p = _clean(path)
    log(f"Esecuzione list_files su '{server}', percorso: {p}")
    with _ftp(server) as ftp:
        try:
            rows = []
            for name, facts in ftp.mlsd(p):
                if name in (".", ".."):
                    continue
                kind = "DIR " if facts.get("type") == "dir" else "FILE"
                rows.append(f"{kind} {name}  {facts.get('size', '')}")
            return "\n".join(sorted(rows)) or "(vuota)"
        except all_errors as e:
            log(f"mlsd fallito su {p} ({e}), riprovo con LIST standard.")
            lines = []
            ftp.retrlines(f"LIST {p}", lines.append)
            return "\n".join(lines) or "(vuota)"


@mcp.tool()
def read_file(server: str, path: str, offset: int = 0, length: int = MAX_CHARS) -> str:
    """Legge un file di testo. Per file grandi usa offset/length per leggere a blocchi."""
    p = _clean(path)
    length = max(1, min(length, MAX_CHARS))
    log(f"Esecuzione read_file su '{server}', percorso: {p}")
    with _ftp(server) as ftp:
        text = _decode(_download(ftp, p))
    chunk = text[offset: offset + length]
    end = offset + len(chunk)
    note = f"[caratteri {offset}-{end} di {len(text)}]"
    if end < len(text):
        note += f" Per continuare usa offset={end}."
    return f"{note}\n{chunk}"


@mcp.tool()
def find_in_file(server: str, path: str, query: str, context_lines: int = 2, max_matches: int = 20) -> str:
    """Cerca un testo (senza distinguere maiuscole) in un file e mostra le righe intorno."""
    p = _clean(path)
    log(f"Esecuzione find_in_file su '{server}', file: {p}, ricerca: '{query}'")
    with _ftp(server) as ftp:
        lines = _decode(_download(ftp, p)).splitlines()
    q = query.lower()
    out, found = [], 0
    for i, line in enumerate(lines):
        if q in line.lower():
            found += 1
            a, b = max(0, i - context_lines), min(len(lines), i + context_lines + 1)
            out.append(f"--- riga {i + 1} ---")
            out.extend(f"{n + 1}: {lines[n]}" for n in range(a, b))
            if found >= max_matches:
                out.append("(altri risultati omessi)")
                break
    return "\n".join(out) or "Nessun risultato."


# ---------- strumenti di scrittura (richiedono READ_ONLY=false) ----------

@mcp.tool()
def write_file(server: str, path: str, content: str) -> str:
    """Crea o sostituisce un file di testo. Prima salva un backup accanto al file.
    Nota: fermare il server di gioco prima di modificare i file della missione."""
    _require_write()
    p = _clean(path)
    log(f"Esecuzione write_file su '{server}', percorso: {p}")
    with _ftp(server) as ftp:
        backup = _safe_write(ftp, p, content.encode("utf-8"))
    return f"File scritto e verificato: {p}" + (f"\nBackup: {backup}" if backup else "\n(nuovo file, nessun backup)")


@mcp.tool()
def replace_in_file(server: str, path: str, old: str, new: str) -> str:
    """Sostituisce un testo esatto (deve comparire una sola volta) senza riscrivere tutto il file. Crea un backup."""
    _require_write()
    p = _clean(path)
    log(f"Esecuzione replace_in_file su '{server}', percorso: {p}")
    with _ftp(server) as ftp:
        text = _decode(_download(ftp, p))
        n = text.count(old)
        if n != 1:
            raise ValueError(f"Il testo da sostituire compare {n} volte (deve comparire esattamente 1 volta).")
        backup = _safe_write(ftp, p, text.replace(old, new).encode("utf-8"))
    return f"Modifica applicata e verificata: {p}\nBackup: {backup}"


@mcp.tool()
def make_dir(server: str, path: str) -> str:
    """Crea una cartella."""
    _require_write()
    p = _clean(path)
    log(f"Esecuzione make_dir su '{server}', percorso: {p}")
    with _ftp(server) as ftp:
        ftp.mkd(p)
    return f"Cartella creata: {p}"


@mcp.tool()
def move_file(server: str, source: str, destination: str) -> str:
    """Sposta o rinomina un file o una cartella."""
    _require_write()
    s, d = _clean(source), _clean(destination)
    log(f"Esecuzione move_file su '{server}', da {s} a {d}")
    with _ftp(server) as ftp:
        if _exists(ftp, d):
            raise FileExistsError(f"La destinazione esiste già: {d}")
        ftp.rename(s, d)
    return f"Spostato: {s} -> {d}"


@mcp.tool()
def delete_file(server: str, path: str) -> str:
    """'Elimina' un file rinominandolo con suffisso .deleted-<data>, così si può recuperare."""
    _require_write()
    p = _clean(path)
    new_name = f"{p}.deleted-{_ts()}"
    log(f"Esecuzione delete_file su '{server}', percorso originale: {p}")
    with _ftp(server) as ftp:
        ftp.rename(p, new_name)
    return f"File messo da parte (recuperabile): {new_name}"


# ---------- backup su GitHub (sola lettura sul server di gioco) ----------

def _gh(method: str, path: str, body=None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}{path}",
        method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "nitrado-mcp-backup",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub {method} {path}: {e.code} {e.read()[:300]!r}") from None


def _git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _walk(ftp: FTP, root: str):
    """Restituisce (percorso_assoluto, dimensione) dei file da salvare sotto root."""
    out, todo = [], [root]
    while todo:
        d = todo.pop()
        for name, facts in ftp.mlsd(d):
            if name in (".", ".."):
                continue
            full = posixpath.join(d, name)
            if facts.get("type") == "dir":
                if name not in BACKUP_SKIP_DIRS:
                    todo.append(full)
                continue
            size = int(facts.get("size", 0) or 0)
            if (name.lower().endswith(BACKUP_EXTS) and size <= BACKUP_MAX_BYTES
                    and not any(s in name for s in BACKUP_SKIP)):
                out.append((full, size))
    return sorted(out)


@mcp.tool()
def backup_to_github(server: str, message: str = "") -> str:
    """Copia su GitHub i file di configurazione della missione (xml/json) del server indicato,
    nella cartella con lo stesso nome del server. Legge soltanto dal server di gioco: non lo
    ferma, non lo riavvia e non modifica nulla. Carica solo i file cambiati."""
    if not (GITHUB_TOKEN and GITHUB_REPO):
        raise RuntimeError("Backup GitHub non configurato: imposta GITHUB_TOKEN e GITHUB_REPO su Render.")
    name = server.strip().lower()
    log(f"Esecuzione backup_to_github su '{name}'")
    with _ftp(name) as ftp:
        missions = [n for n, f in ftp.mlsd("/dayzps_missions") if f.get("type") == "dir" and n not in (".", "..")]
        if len(missions) != 1:
            raise RuntimeError(f"Attesa una sola cartella missione, trovate: {missions}")
        root = f"/dayzps_missions/{missions[0]}"
        files = {}
        for path, _size in _walk(ftp, root):
            files[f"{name}/{posixpath.relpath(path, root)}"] = _download(ftp, path)

    ref = _gh("GET", f"/git/ref/heads/{GITHUB_BRANCH}")
    base_commit = ref["object"]["sha"]
    base_tree = _gh("GET", f"/git/commits/{base_commit}")["tree"]["sha"]
    existing = {e["path"]: e["sha"] for e in _gh("GET", f"/git/trees/{base_tree}?recursive=1").get("tree", [])
                if e.get("type") == "blob"}

    changed = [p for p, data in files.items() if existing.get(p) != _git_blob_sha(data)]
    if not changed:
        return f"{name}: nessuna modifica rispetto all'ultimo backup ({len(files)} file controllati)."

    tree = []
    for p in changed:
        blob = _gh("POST", "/git/blobs", {"content": base64.b64encode(files[p]).decode(), "encoding": "base64"})
        tree.append({"path": p, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    new_tree = _gh("POST", "/git/trees", {"base_tree": base_tree, "tree": tree})["sha"]
    msg = message.strip() or f"Backup automatico {name} {time.strftime('%Y-%m-%d %H:%M')}"
    commit = _gh("POST", "/git/commits", {"message": msg, "tree": new_tree, "parents": [base_commit]})["sha"]
    _gh("PATCH", f"/git/refs/heads/{GITHUB_BRANCH}", {"sha": commit})
    shown = "\n".join(f"- {p}" for p in changed[:40])
    more = f"\n... e altri {len(changed) - 40}" if len(changed) > 40 else ""
    return f"{name}: {len(changed)} file aggiornati su GitHub (commit {commit[:7]}).\n{shown}{more}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")