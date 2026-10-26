"""
Connettore MCP per i server Nitrado (DayZ console) tramite FTP.

- Supporta fino a 9 server, configurati con variabili d'ambiente (S1_*, S2_*, ...).
- Ogni modifica crea prima una copia di backup accanto al file originale.
- Le credenziali NON stanno nel codice: solo variabili d'ambiente su Render.
- READ_ONLY=true (default) blocca ogni scrittura. Mettilo a false per abilitarla.
- Include log di diagnostica dettagliati per analizzare errori di timeout e connessione.
"""

import io
import os
import posixpath
import sys
import time
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


if __name__ == "__main__":
    mcp.run(transport="streamable-http")