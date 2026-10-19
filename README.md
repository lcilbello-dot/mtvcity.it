# MTV City · calendario automatico

Automazione del calendario di [mtvcity.it](https://mtvcity.it), progetto indipendente dedicato a Montevecchia, nella Brianza lecchese.

Ogni giorno alle **06:17, Europe/Rome**, GitHub Actions legge i calendari pubblici di Comune di Montevecchia, ProMontevecchia e Parco di Montevecchia e Valle del Curone. Le ricorrenze sono espanse entro 180 giorni; modifiche, annullamenti e scadenze aggiornano le schede senza conferma manuale. La pianificazione può subire ritardi.

Si pubblicano dati espliciti verificabili: titoli, date, luoghi e collegamenti ufficiali. Descrizioni pratiche IT/EN provengono da modelli fissi. Fotografie e descrizioni editoriali delle fonti non vengono importate. Date discordanti e dati insufficienti restano fuori dal calendario del sito. La presenza nel calendario non sostituisce le indicazioni dell'organizzatore.

## Dati per il sito

Il file destinato al sito è [public/assets/data/events-feed.json](public/assets/data/events-feed.json), generato dal primo controllo. Collegamento pubblico:

`https://raw.githubusercontent.com/lcilbello-dot/mtvcity.it/main/public/assets/data/events-feed.json`

Il sito lo rilegge ogni cinque minuti; conserva l'ultima copia valida in caso di errore temporaneo. Gli eventi futuri non riconfermati per 72 ore vengono sospesi. Nessuna credenziale di hosting è necessaria. Questa repository non contiene il sito PHP, fotografie, configurazioni private o copie complete delle pagine sorgenti.

## Controlli e segnalazioni

Il flusso verifica ricorrenze, cambio dell'ora, eccezioni, annullamenti e integrità del catalogo prima del commit. Una nuova anomalia apre o aggiorna una issue assegnata al proprietario; anomalie identiche non generano commenti ripetuti. Le notifiche dipendono dalle preferenze GitHub dell'account. Ogni esecuzione conserva un rapporto per 14 giorni. Le bozze nel JSON sono dati di fonti pubbliche e non vengono mostrate dal sito.

La modalità predefinita è `automatic`. Impostare la variabile `EVENTS_PUBLISH_MODE=review` soltanto se si desidera una conferma tramite pull request.

Per una verifica locale:

```sh
python -m pip install -r requirements.txt
python -m unittest -v test_automation
python monitor_events.py
python validate_feed.py
```

Senza `--apply`, il monitor produce soltanto una proposta nella cartella `run/`. `events-feed.json` conserva lo stato del catalogo e `state.json` le impronte dei controlli. `seed-events.json` mantiene gli identificativi delle schede già presenti e i conflitti editoriali conosciuti. `sources.json` elenca le sole fonti autorizzate.

Proprietario del progetto: Luca Ceresa · info@mtvcity.it. La visibilità pubblica del repository non assegna una licenza alle fotografie del sito, che non sono incluse.
