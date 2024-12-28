# RSS Archiver

## **Descrizione**

**RSS Archiver** è un programma Python progettato per scaricare, gestire e archiviare articoli provenienti da feed RSS. Utilizza un'interfaccia utente testuale basata su `curses` per facilitare la navigazione, la ricerca e la gestione degli articoli. Gli articoli vengono salvati in un database SQLite e possono essere archiviati automaticamente dopo un determinato periodo. Inoltre, il programma supporta la stampa degli articoli tramite una stampante di rete configurata.

## **Caratteristiche Principali**

- **Gestione dei Feed RSS**: Aggiungi, elimina e rinomina feed RSS facilmente tramite l'interfaccia utente.
- **Salvataggio degli Articoli**: Scarica e salva automaticamente gli articoli dai feed RSS in un database SQLite.
- **Ricerca per Tag**: Cerca articoli basati su tag personalizzati per una facile organizzazione.
- **Archiviazione Automatica**: Archivia articoli obsoleti dopo un periodo definito, esportandoli in file JSON compressi.
- **Interfaccia Utente Intuitiva**: Naviga tra le fonti, visualizza gli articoli e gestisci le operazioni tramite un'interfaccia `curses`.
- **Stampa degli Articoli**: Invia articoli direttamente a una stampante di rete configurata.

## **Installazione**

### **1. Requisiti di Sistema**

- **Python 3.6 o superiore**
- **Pip** (gestore di pacchetti Python)
- **CUPS** (Common UNIX Printing System) per la gestione della stampante

### **2. Clonazione del Repository**

Scarica o clona il repository del programma:

git clone https://github.com/tuo-username/rss_archiver.git
cd rss_archiver

### **3. Installazione delle Dipendenze

Installa le dipendenze necessarie utilizzando pip:

pip3 install feedparser html2text python-dateutil requests beautifulsoup4

### **4. Configurazione della Stampante

Assicurati che la tua stampante di rete sia configurata correttamente in CUPS.

    Verifica le Stampanti Disponibili:

lpstat -p

Configura la Variabile PRINTER_NAME:

Nel file rss_archiver.py, verifica che la variabile PRINTER_NAME corrisponda al nome della tua stampante configurata in CUPS.

    PRINTER_NAME = "Canon"  # Sostituisci con il nome corretto della tua stampante

### **Utilizzo
### **1. Preparazione del Sistema

Prima di eseguire il programma, assicurati che le directory necessarie siano presenti e che i permessi siano corretti.

mkdir -p db config logs archive
chmod 755 db config logs archive
chmod 644 config/feeds.txt logs/archiver.log

### **2. Avvio dell'Interfaccia Utente

Per avviare l'interfaccia utente curses, esegui:

python3 rss_archiver.py

### **3. Opzioni da Riga di Comando

Il programma supporta diverse opzioni per operazioni non interattive:

    Aggiornamento dei Feed: Scarica nuovi articoli senza avviare l'interfaccia utente.

python3 rss_archiver.py --update

Archiviazione degli Articoli: Archivia articoli obsoleti senza avviare l'interfaccia utente.

python3 rss_archiver.py --archive

Aggiornamento e Archiviazione Contemporaneamente:

    python3 rss_archiver.py --archive --update

### **4. Navigazione nell'Interfaccia Utente

Una volta avviata l'interfaccia, vedrai un menu con diverse opzioni:

    Visualizza Articoli per Fonte: Seleziona una fonte RSS e visualizza gli articoli associati.
    Cerca Articoli: Cerca articoli basati su tag personalizzati.
    Aggiungi Feed RSS: Aggiungi un nuovo feed RSS inserendo l'URL.
    Aggiorna Articoli: Scarica nuovi articoli dai feed RSS aggiunti.
    Gestisci Feed RSS: Elenca, elimina o rinomina i feed RSS.
    Archivia Articoli Obsoleti: Archivia automaticamente gli articoli più vecchi di una soglia definita.
    Esci: Chiudi l'applicazione.

### **5. Aggiungere Fonti RSS

    Seleziona l'opzione "Aggiungi Feed RSS".
    Inserisci l'URL del feed RSS che desideri aggiungere.
    Il programma tenterà di estrarre il titolo del feed per usarlo come nome della fonte.

### **6. Ricerca per Tag

    Seleziona l'opzione "Cerca Articoli".
    Inserisci uno o più tag separati da virgola.
    Visualizza i risultati corrispondenti e seleziona un articolo per ulteriori azioni.

### **7. Archiviazione Automatica

Il programma può archiviare automaticamente gli articoli obsoleti (es. più vecchi di 30 giorni) eseguendo:

python3 rss_archiver.py --archive

Gli articoli archiviati vengono salvati nella directory archive/ organizzati per anno e mese.

### **8. Stampa degli Articoli

Durante la visualizzazione di un articolo, puoi scegliere l'opzione "Stampa" per inviarlo direttamente alla stampante di rete configurata.
Automatizzazione con Cron

Per automatizzare l'aggiornamento e l'archiviazione degli articoli, puoi configurare cron:

    Apri il Crontab:

crontab -e

Aggiungi una Riga per Eseguire lo Script Periodicamente:

Ad esempio, per eseguire l'aggiornamento e l'archiviazione ogni giorno alle 6:00 AM:

    0 6 * * * /usr/bin/python3 /percorso/del/tuo/script/rss_archiver.py --archive --update >> /percorso/del/tuo/log/cron_archiver.log 2>&1

    Note:
        Sostituisci /percorso/del/tuo/script/ con il percorso effettivo dove si trova il tuo script.
        Reindirizza l'output e gli errori a un file di log per monitorare l'esecuzione.

    Salva e Chiudi l'Editor.

Struttura delle Directory

    db/: Contiene il database SQLite (rss_archiver.db) dove vengono salvati gli articoli e le fonti RSS.
    config/: Contiene il file feeds.txt con gli URL dei feed RSS aggiunti.
    logs/: Contiene il file di log (archiver.log) per monitorare le operazioni e gli errori.
    archive/: Contiene gli articoli archiviati in file JSON compressi organizzati per anno e mese.

Permessi dei File

Assicurati che il programma abbia i permessi necessari per leggere e scrivere nelle directory e nei file:

chmod 755 db config logs archive
chmod 644 config/feeds.txt logs/archiver.log
chmod 755 archive/

Verifica del Funzionamento

    Aggiungi un Feed RSS tramite l'interfaccia utente e verifica che l'URL sia aggiunto sia al database che a config/feeds.txt.

    Esegui l'Aggiornamento:

python3 rss_archiver.py --update

Controlla il log in logs/archiver.log per assicurarti che i feed siano stati letti e gli articoli scaricati correttamente.

Esegui l'Archiviazione:

    python3 rss_archiver.py --archive

    Verifica che gli articoli obsoleti siano stati archiviati nella directory archive/ e rimossi dal database principale.

    Controlla feeds.txt:

    Assicurati che config/feeds.txt contenga tutti gli URL dei feed aggiunti.

Risorse Utili

    Curses Documentation: https://docs.python.org/3/library/curses.html
    Feedparser Documentation: https://feedparser.readthedocs.io/
    BeautifulSoup Documentation: https://www.crummy.com/software/BeautifulSoup/bs4/doc/
    CUPS Documentation: https://www.cups.org/documentation.php

Troubleshooting

    Permessi Negati: Verifica che il programma abbia i permessi necessari per accedere e modificare i file e le directory richieste.

    Problemi di Connessione alla Stampante: Assicurati che la stampante sia correttamente configurata in CUPS e che la variabile PRINTER_NAME nel tuo script corrisponda al nome della stampante configurata.

    Feed RSS Non Aggiornati: Controlla il file di log logs/archiver.log per eventuali errori durante il fetching dei feed. Verifica che gli URL dei feed siano corretti e accessibili.

Se desideri contribuire allo sviluppo di RSS Archiver, sentiti libero di inviare pull request o segnalare issue nel repository GitHub.
