# Connettore Nitrado FTP per Claude

Permette a Claude di vedere (e, se lo abiliti, modificare) i file dei tuoi server Nitrado via FTP.
Ogni modifica crea prima un backup `nomefile.bak-AAAAMMGG-HHMMSS` accanto al file, poi verifica che il file caricato coincida.

## 1. Cosa mettere su Render

Nel servizio Render che già usi (o in uno nuovo) sostituisci i file con questi tre:
`server.py`, `requirements.txt`, `README.md`.

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `python server.py`
- **Variabile `PYTHON_VERSION`:** `3.12.3` (se Render non la imposta da solo)

## 2. Variabili d'ambiente (Environment su Render)

Non scrivere mai password o chiavi nel codice o in chat.

| Variabile | Valore |
|---|---|
| `SECRET_PATH` | stringa lunga e casuale, fa parte dell'indirizzo del connettore (es. generata con `python -c "import secrets;print(secrets.token_urlsafe(32))"`) |
| `READ_ONLY` | `true` all'inizio. Mettila a `false` quando la lettura funziona e vuoi abilitare le modifiche |
| `S1_NAME` | nome corto del server 1 (es. `nasdara`) |
| `S1_HOST` | host FTP dalla dashboard Nitrado |
| `S1_PORT` | porta FTP (di solito `21`) |
| `S1_USER` | utente FTP |
| `S1_PASS` | password FTP |

Per il secondo e il terzo server ripeti con `S2_...` e `S3_...` (fino a `S9_...`).

### Backup su GitHub (facoltativo)

Lo strumento `backup_to_github` copia i file di configurazione della missione (`.xml`, `.json`) in un repository GitHub, nella cartella con il nome del server. Legge soltanto dal server di gioco: non lo ferma e non lo riavvia. Carica solo i file cambiati.

| Variabile | Valore |
|---|---|
| `GITHUB_TOKEN` | token GitHub *fine-grained* con accesso **solo** al repository di backup, permesso *Contents: Read and write* |
| `GITHUB_REPO` | repository di backup, es. `utente/califfato-server-config` |
| `GITHUB_BRANCH` | facoltativo, default `main` |
| `BACKUP_MAX_BYTES` | facoltativo, dimensione massima di un file da salvare (default 3 MB) |
### Discord nel cloud (funziona anche a PC spento)

| Variabile | Valore |
|---|---|
| `DISCORD_BOT_TOKEN` | token del bot (Discord Developer Portal → la tua applicazione → *Bot* → *Reset Token*). Il bot deve essere già nei server Discord |
| `DISCORD_GUILDS` | ID dei server Discord autorizzati, separati da virgola (es. `1404777365590249513,1145317853214552064`). Gli altri vengono rifiutati |

Strumenti: `discord_list_guilds`, `discord_list_channels`, `discord_read_messages`, `discord_send_message` (con `allow_everyone` per far notificare @everyone e `reply_to` per rispondere), `discord_send_embed`, `discord_edit_message`, `discord_delete_message` (solo messaggi del bot).

### API Nitrado: stato e log (sola lettura)

| Variabile | Valore |
|---|---|
| `NITRADO_TOKEN` | *Long-life token* da server.nitrado.net → Il mio account → Developer Portal / Long-life tokens (permesso `service`) |
| `S1_SERVICE_ID`, `S2_SERVICE_ID`, … | ID del servizio Nitrado di ogni server (es. Chernarus `17731864`, si vede nell'indirizzo del pannello web) |

Strumenti: `nitrado_status`, `nitrado_list_logs`, `nitrado_read_log` (con filtro `grep`). Nessuno strumento ferma o riavvia i server.

### File grandi senza browser

`upload_from_github` prende un file dal repository di backup (`GITHUB_REPO`) e lo carica sul server via FTP, con backup e verifica. Serve che il token GitHub abbia *Contents: Read and write* (già richiesto per i backup).

Host, porta, utente e password FTP li trovi nella dashboard Nitrado del singolo servizio, sezione FTP: sono diversi per ogni server.

## 3. Collegarlo a Claude

L'indirizzo da aggiungere come connettore personalizzato è:

```
https://NOME-DEL-TUO-SERVIZIO.onrender.com/SECRET_PATH/mcp
```

(al posto di `SECRET_PATH` metti il valore della variabile). Questo indirizzo è come una password: non condividerlo.
Se riusi il servizio Render esistente e metti in `SECRET_PATH` lo stesso valore del vecchio indirizzo, il connettore in Claude non va riconfigurato.

## 3b. Collegarlo anche a ChatGPT (facoltativo)

Su Render aggiungi la variabile `SECRET_PATH_GPT` con un'altra stringa lunga e casuale, **diversa** da `SECRET_PATH`.
Lo stesso servizio espone allora un secondo indirizzo, solo per ChatGPT:

```
https://NOME-DEL-TUO-SERVIZIO.onrender.com/SECRET_PATH_GPT/mcp
```

- Ha tutti gli strumenti tranne `delete_file`, `move_file`, `copy_between_servers` e `discord_delete_message`.
- L'indirizzo di Claude non cambia.
- Per togliere l'accesso a ChatGPT cancella `SECRET_PATH_GPT` su Render: l'indirizzo smette subito di funzionare.

In ChatGPT: *Impostazioni → App e connettori → Avanzate → Modalità sviluppatore*, poi *Crea*, incolla l'indirizzo e scegli **nessuna autenticazione** (la protezione è l'indirizzo segreto, quindi non condividerlo).

## 4. Test consigliato

1. Con `READ_ONLY=true`: chiedi a Claude di elencare i server, poi i file della cartella `dayzps_missions/dayzOffline.chernarusplus`.
2. Quando la lettura funziona, metti `READ_ONLY=false`.
3. Prima di modificare qualsiasi file della missione, **ferma il server di gioco**; riavvialo dopo.

## Strumenti disponibili

Lettura: `list_servers`, `list_files`, `read_file`, `find_in_file`.
Scrittura (solo con `READ_ONLY=false`): `write_file`, `replace_in_file`, `make_dir`, `move_file`, `delete_file` (rinomina con suffisso `.deleted-…`, non cancella davvero), `copy_between_servers` (copia file o cartelle intere da un server all'altro, anche grandi o binari; salta `.bak-`/`.deleted-` e i backup del killfeed, rinomina in `.bak-…` i file che sovrascrive, si può rilanciare per riprendere; con `dry_run=true` mostra solo cosa farebbe).
