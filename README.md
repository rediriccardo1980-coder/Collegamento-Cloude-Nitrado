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
Host, porta, utente e password FTP li trovi nella dashboard Nitrado del singolo servizio, sezione FTP: sono diversi per ogni server.

## 3. Collegarlo a Claude

L'indirizzo da aggiungere come connettore personalizzato è:

```
https://NOME-DEL-TUO-SERVIZIO.onrender.com/SECRET_PATH/mcp
```

(al posto di `SECRET_PATH` metti il valore della variabile). Questo indirizzo è come una password: non condividerlo.
Se riusi il servizio Render esistente e metti in `SECRET_PATH` lo stesso valore del vecchio indirizzo, il connettore in Claude non va riconfigurato.

## 4. Test consigliato

1. Con `READ_ONLY=true`: chiedi a Claude di elencare i server, poi i file della cartella `dayzps_missions/dayzOffline.chernarusplus`.
2. Quando la lettura funziona, metti `READ_ONLY=false`.
3. Prima di modificare qualsiasi file della missione, **ferma il server di gioco**; riavvialo dopo.

## Strumenti disponibili

Lettura: `list_servers`, `list_files`, `read_file`, `find_in_file`.
Scrittura (solo con `READ_ONLY=false`): `write_file`, `replace_in_file`, `make_dir`, `move_file`, `delete_file` (rinomina con suffisso `.deleted-…`, non cancella davvero).
