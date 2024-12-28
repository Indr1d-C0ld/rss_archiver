#!/usr/bin/env python3
import feedparser
import sqlite3
import curses
import html2text
import os
import subprocess
import logging
from datetime import datetime, timedelta
import dateutil.parser   # Per fare il parsing delle date in formati diversi
import requests
from bs4 import BeautifulSoup
import argparse
import time
import json
import gzip

# Ottieni il percorso della directory dello script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Configurazioni principali con percorsi assoluti
DB_PATH = os.path.join(SCRIPT_DIR, "db", "rss_archiver.db")
FEEDS_FILE = os.path.join(SCRIPT_DIR, "config", "feeds.txt")
LOG_FILE = os.path.join(SCRIPT_DIR, "logs", "archiver.log")
ARCHIVE_DIR = os.path.join(SCRIPT_DIR, "archive")
PRINTER_NAME = "Canon"  # Nome della stampante configurata in CUPS
CACHE_DURATION_HOURS = 24  # Intervallo di tempo per ricaricare gli articoli (in ore)
ARCHIVE_THRESHOLD_DAYS = 30  # Soglia in giorni per archiviare gli articoli

# Configurazione del logging
logging.basicConfig(
    filename=LOG_FILE, 
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ----------------------------------------------------------------------------
# FUNZIONI PER IL DATABASE
# ----------------------------------------------------------------------------

def initialize_db(db_name=DB_PATH):
    """
    Inizializza il database SQLite creando le tabelle necessarie.
    """
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    # Crea la tabella 'sources' se non esiste
    c.execute('''
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            url TEXT UNIQUE
        )
    ''')
    # Crea la tabella 'articles' se non esiste, includendo 'source_id'
    c.execute('''
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            link TEXT UNIQUE,
            published TEXT,
            content TEXT,
            scraped_at TEXT,
            source_id INTEGER,
            FOREIGN KEY(source_id) REFERENCES sources(id)
        )
    ''')
    # Crea la tabella 'tags' se non esiste
    c.execute('''
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag TEXT UNIQUE
        )
    ''')
    # Crea la tabella 'article_tags' se non esiste
    c.execute('''
        CREATE TABLE IF NOT EXISTS article_tags (
            article_id INTEGER,
            tag_id INTEGER,
            FOREIGN KEY(article_id) REFERENCES articles(id),
            FOREIGN KEY(tag_id) REFERENCES tags(id),
            PRIMARY KEY (article_id, tag_id)
        )
    ''')
    conn.commit()
    conn.close()
    logging.info("Database initialized.")

def save_source(db_name, name, url):
    """
    Salva una fonte RSS nel database.
    Se la fonte esiste già, restituisce il suo ID.
    """
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    try:
        c.execute('INSERT INTO sources (name, url) VALUES (?, ?)', (name, url))
        source_id = c.lastrowid
        logging.info(f"Source saved: {name} ({url})")
    except sqlite3.IntegrityError:
        # Se la fonte esiste già, recuperiamo il suo ID
        c.execute('SELECT id FROM sources WHERE url = ?', (url,))
        row = c.fetchone()
        if row:
            source_id = row[0]
            logging.info(f"Source already exists: {name} ({url})")
        else:
            source_id = None
    conn.commit()
    conn.close()
    return source_id

def update_source_name(db_name, source_id, new_name):
    """
    Aggiorna il nome di una fonte RSS.
    """
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    c.execute('UPDATE sources SET name = ? WHERE id = ?', (new_name, source_id))
    conn.commit()
    conn.close()
    logging.info(f"Source ID {source_id} renamed to {new_name}")

def delete_source(db_name, source_id):
    """
    Elimina una fonte RSS dal database.
    """
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    c.execute('DELETE FROM sources WHERE id = ?', (source_id,))
    conn.commit()
    conn.close()
    logging.info(f"Source ID {source_id} deleted")

def save_article(db_name, title, link, published, content, source_id, scraped_now=False):
    """
    Salva un articolo nel database.
    Evita il salvataggio duplicato controllando il campo 'link' come UNIQUE.
    Aggiorna 'content' e 'scraped_at' se 'scraped_now' è True.
    """
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    
    try:
        if scraped_now:
            c.execute('''
                INSERT INTO articles (title, link, published, content, scraped_at, source_id) 
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (title, link, published, content, datetime.utcnow().isoformat(), source_id))
            article_id = c.lastrowid
            logging.info(f"Article saved and scraped: {title}")
        else:
            c.execute('''
                INSERT INTO articles (title, link, published, content, scraped_at, source_id) 
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (title, link, published, content, datetime.utcnow().isoformat(), source_id))
            article_id = c.lastrowid
            logging.info(f"Article saved: {title}")
    except sqlite3.IntegrityError:
        # Se l'articolo esiste già, aggiorniamo il contenuto se necessario
        if scraped_now:
            c.execute('''
                UPDATE articles 
                SET content = ?, scraped_at = ?, source_id = ?
                WHERE link = ?
            ''', (content, datetime.utcnow().isoformat(), source_id, link))
            article_id = c.execute('SELECT id FROM articles WHERE link = ?', (link,)).fetchone()[0]
            logging.info(f"Article updated and re-scraped: {title}")
        else:
            # Se l'articolo esiste già e non è un aggiornamento, recuperiamo il suo ID
            c.execute('SELECT id FROM articles WHERE link = ?', (link,))
            row = c.fetchone()
            if row:
                article_id = row[0]
                logging.info(f"Article already exists: {title}")
            else:
                article_id = None

    conn.commit()
    conn.close()

# ----------------------------------------------------------------------------
# FUNZIONI PER L'ELABORAZIONE TESTO E FEED
# ----------------------------------------------------------------------------

def fetch_feeds(feed_urls, progress_win=None):
    """
    Scarica i feed RSS dalle URL fornite e restituisce una lista di 'FeedParserDict'.
    Se progress_win è fornito, aggiorna la progress bar e il messaggio corrente.
    """
    feeds = []
    total = len(feed_urls)
    for idx, url in enumerate(feed_urls, start=1):
        try:
            feed = feedparser.parse(url)
            if feed.bozo:
                raise feed.bozo_exception
            feeds.append(feed)
            logging.info(f"Fetched feed: {url}")
            if progress_win:
                feed_title = feed.feed.get('title', 'Unknown Source')
                update_progress_bar(progress_win, idx, total, f"Elaborazione feed: {feed_title}")
        except Exception as e:
            logging.error(f"Error fetching feed {url}: {e}")
            if progress_win:
                update_progress_bar(progress_win, idx, total, f"Errore nel fetch del feed: {url}")
    return feeds

def fetch_full_article(link):
    """
    Effettua lo scraping della pagina web dell'articolo per ottenere il contenuto completo.
    Restituisce il testo estratto o una stringa vuota in caso di fallimento.
    """
    try:
        response = requests.get(link, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Error fetching article content from {link}: {e}")
        return ""
    
    try:
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Tenta di estrarre il contenuto principale dell'articolo.
        # Questo metodo è molto generico e potrebbe non funzionare per tutti i siti.
        # Per una migliore estrazione, considera l'uso di librerie come newspaper3k.
        # Ecco un esempio semplice:
        article = soup.find('article')
        if not article:
            # Fallback: estrai tutto il testo dai tag <p>
            paragraphs = soup.find_all('p')
            text = '\n'.join([para.get_text() for para in paragraphs])
        else:
            paragraphs = article.find_all('p')
            text = '\n'.join([para.get_text() for para in paragraphs])
        
        return text
    except Exception as e:
        logging.error(f"Error parsing article content from {link}: {e}")
        return ""

def process_feeds(db_name, progress_win=None):
    """
    Legge la lista di feed dal file FEEDS_FILE, li scarica e salva gli articoli nel DB.
    Converte l'HTML in testo semplice grazie a html2text e, se necessario, effettua lo scraping per ottenere il contenuto completo.
    Implementa un sistema di caching per evitare di scaricare ripetutamente lo stesso articolo.
    """
    feed_urls = read_feeds()
    feeds = fetch_feeds(feed_urls, progress_win)
    h = html2text.HTML2Text()
    h.ignore_links = True
    h.ignore_images = True

    total_feeds = len(feeds)
    for feed_idx, feed in enumerate(feeds, start=1):
        source_name = feed.feed.get('title', 'Unknown Source')
        source_url = feed.feed.get('link', 'Unknown URL')
        source_id = save_source(db_name, source_name, source_url)

        for entry in feed.entries:
            title = entry.title
            link = entry.link
            
            # Tenta di recuperare la data di pubblicazione
            if 'published' in entry:
                published = entry.published
            elif 'updated' in entry:
                published = entry.updated
            else:
                published = ''
            
            # Controlla se l'articolo è già presente nel DB
            conn = sqlite3.connect(db_name)
            c = conn.cursor()
            c.execute('SELECT content, scraped_at FROM articles WHERE link = ?', (link,))
            row = c.fetchone()
            conn.close()

            needs_scraping = True
            if row:
                content_existing, scraped_at_str = row
                if content_existing and len(content_existing) >= 200:
                    # Verifica se il cache è ancora valida
                    if scraped_at_str:
                        scraped_at = dateutil.parser.parse(scraped_at_str)
                        if datetime.utcnow() - scraped_at < timedelta(hours=CACHE_DURATION_HOURS):
                            needs_scraping = False
            if needs_scraping:
                # Recupera il contenuto completo dell'articolo
                full_content = fetch_full_article(link)
                if full_content:
                    content = full_content
                    scraped_now = True
                else:
                    # Se non riesce a ottenere il contenuto completo, usa il summary
                    if hasattr(entry, 'content') and len(entry.content) > 0:
                        raw_content = entry.content[0].value
                        content = h.handle(raw_content)
                    else:
                        raw_content = entry.get('summary', '')
                        content = h.handle(raw_content)
                    scraped_now = False
            else:
                # Usa il contenuto esistente
                content = row[0]
                scraped_now = False

            # Salviamo nel DB (inserimento o aggiornamento)
            save_article(db_name, title, link, published, content, source_id, scraped_now)

# ----------------------------------------------------------------------------
# FUNZIONI PER L'ARCHIVIAZIONE DEGLI ARTICOLI
# ----------------------------------------------------------------------------

def archive_old_articles(db_name, threshold_days=ARCHIVE_THRESHOLD_DAYS):
    """
    Archivia gli articoli più vecchi di 'threshold_days' giorni.
    Gli articoli archiviati vengono esportati in file JSON compressi,
    organizzati per anno e mese, e poi rimossi dal database principale.
    """
    cutoff_date = datetime.utcnow() - timedelta(days=threshold_days)
    cutoff_iso = cutoff_date.isoformat()

    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    c.execute('''
        SELECT id, title, link, published, content, source_id FROM articles
        WHERE published IS NOT NULL AND datetime(published) < ?
    ''', (cutoff_iso,))
    old_articles = c.fetchall()
    conn.close()

    if not old_articles:
        logging.info("Nessun articolo da archiviare.")
        return

    # Organizza gli articoli per anno e mese
    archive_dict = {}
    for article in old_articles:
        id_, title, link, published, content, source_id = article
        try:
            pub_datetime = dateutil.parser.parse(published)
            year = pub_datetime.year
            month = pub_datetime.month
            key = f"{year}/{month:02d}"
            if key not in archive_dict:
                archive_dict[key] = []
            archive_dict[key].append({
                'id': id_,
                'title': title,
                'link': link,
                'published': published,
                'content': content,
                'source_id': source_id
            })
        except Exception as e:
            logging.error(f"Errore nel parsing della data per l'articolo ID {id_}: {e}")

    # Salva gli articoli in file JSON compressi
    for key, articles in archive_dict.items():
        year, month = key.split('/')
        archive_path = os.path.join(ARCHIVE_DIR, year, month)
        os.makedirs(archive_path, exist_ok=True)
        # Nome del file basato sulla data odierna e su un timestamp
        today = datetime.utcnow().strftime("%Y_%m_%d")
        timestamp = datetime.utcnow().strftime("%H%M%S")
        file_name = f"articles_{today}_{timestamp}.json.gz"
        file_path = os.path.join(archive_path, file_name)

        try:
            with gzip.open(file_path, 'wt', encoding='utf-8') as f:
                json.dump(articles, f, ensure_ascii=False, indent=2)
            logging.info(f"Archiviati {len(articles)} articoli in {file_path}")
        except Exception as e:
            logging.error(f"Errore nell'archiviazione degli articoli in {file_path}: {e}")

    # Rimuovi gli articoli archiviati dal database principale
    article_ids = [article[0] for article in old_articles]
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    c.executemany('DELETE FROM articles WHERE id = ?', [(id_,) for id_ in article_ids])
    conn.commit()
    conn.close()
    logging.info(f"Rimossi {len(article_ids)} articoli dal database principale.")

# ----------------------------------------------------------------------------
# FUNZIONI PER LA GESTIONE DEI FEED (LETTORE/SCRITTURA FILE)
# ----------------------------------------------------------------------------

def add_feed(url):
    """
    Aggiunge un feed al file di testo FEEDS_FILE.
    """
    with open(FEEDS_FILE, 'a') as f:
        f.write(url + '\n')
    logging.info(f"Added new feed: {url}")

def delete_feed_from_file(url):
    """
    Rimuove un feed dal file di testo FEEDS_FILE.
    """
    if not os.path.exists(FEEDS_FILE):
        return
    with open(FEEDS_FILE, 'r') as f:
        feeds = [line.strip() for line in f if line.strip() and line.strip() != url]
    with open(FEEDS_FILE, 'w') as f:
        for feed in feeds:
            f.write(feed + '\n')
    logging.info(f"Deleted feed from file: {url}")

def read_feeds():
    """
    Legge la lista di feed dal file FEEDS_FILE.
    Crea il file se non esiste.
    """
    if not os.path.exists(FEEDS_FILE):
        os.makedirs(os.path.dirname(FEEDS_FILE), exist_ok=True)
        open(FEEDS_FILE, 'w').close()
        logging.info(f"Created empty feeds file at {FEEDS_FILE}")
    try:
        with open(FEEDS_FILE, 'r') as f:
            feeds = [line.strip() for line in f if line.strip()]
        logging.info(f"Read {len(feeds)} feeds from {FEEDS_FILE}")
        return feeds
    except Exception as e:
        logging.error(f"Error reading feeds from {FEEDS_FILE}: {e}")
        return []

# ----------------------------------------------------------------------------
# UTILITY PER L'ORDINAMENTO DELLE DATE
# ----------------------------------------------------------------------------

def parse_date_str(date_str):
    """
    Tenta di convertire la data (stringa) in un oggetto datetime.
    Se fallisce, restituisce None.
    """
    try:
        return dateutil.parser.parse(date_str)
    except:
        return None

# ----------------------------------------------------------------------------
# FUNZIONI PER L'INTERFACCIA TESTUALE (CURSES)
# ----------------------------------------------------------------------------

def ui_main(stdscr, db_name):
    """
    Menu principale dell'applicazione.
    """
    curses.start_color()
    curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
    curses.init_pair(5, curses.COLOR_RED, curses.COLOR_BLACK)
    curses.init_pair(6, curses.COLOR_WHITE, curses.COLOR_BLUE)

    while True:
        stdscr.clear()
        # Aggiungi un bordo
        height, width = stdscr.getmaxyx()
        border_text = " RSS Archiver "
        stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
        stdscr.addstr(0, max((width - len(border_text)) // 2, 0), border_text)
        stdscr.attroff(curses.color_pair(6) | curses.A_BOLD)
        stdscr.border()

        # Menu Opzioni
        menu_options = [
            "1. Visualizza Articoli per Fonte",
            "2. Cerca Articoli",
            "3. Aggiungi Feed RSS",
            "4. Aggiorna Articoli",
            "5. Gestisci Feed RSS",
            "6. Archivia Articoli Obsoleti",
            "7. Esci"
        ]
        start_y = 3
        for idx, option in enumerate(menu_options, start=start_y):
            if option.startswith("5. Gestisci"):
                safe_addstr(stdscr, idx, 2, option, curses.color_pair(4))
            else:
                safe_addstr(stdscr, idx, 2, option, curses.color_pair(2) if idx == start_y else 0)

        # Istruzioni
        instruction = "Seleziona un'opzione premendo il numero corrispondente."
        safe_addstr(stdscr, start_y + len(menu_options) + 2, 2, instruction, curses.color_pair(3))
        stdscr.refresh()

        key = stdscr.getch()

        if key == ord('1'):
            select_source(stdscr, db_name)
        elif key == ord('2'):
            search_ui(stdscr, db_name)
        elif key == ord('3'):
            add_feed_ui(stdscr)
        elif key == ord('4'):
            update_articles_ui(stdscr, db_name)
        elif key == ord('5'):
            manage_feeds_ui(stdscr, db_name)
        elif key == ord('6'):
            archive_articles_ui(stdscr, db_name)
        elif key == ord('7'):
            break
        else:
            # Mostra un messaggio di errore temporaneo
            show_message(stdscr, "Opzione non valida! Premi un tasto per continuare.", curses.color_pair(5))

def select_source(stdscr, db_name):
    """
    Mostra un elenco delle fonti e permette di selezionare una fonte per visualizzare i suoi articoli.
    """
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    c.execute('SELECT id, name FROM sources ORDER BY name ASC')
    sources = c.fetchall()
    conn.close()

    if not sources:
        show_message(stdscr, "Nessuna fonte RSS aggiunta. Aggiungi una fonte prima di procedere.", curses.color_pair(5))
        return

    page = 0
    sources_per_page = 5  # Numero di fonti per pagina
    total_pages = (len(sources) + sources_per_page - 1) // sources_per_page

    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        stdscr.border()
        # Titolo
        title_text = f" Seleziona una Fonte (Pagina {page + 1}/{total_pages}) "
        stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
        stdscr.addstr(0, max((width - len(title_text)) // 2, 0), title_text)
        stdscr.attroff(curses.color_pair(6) | curses.A_BOLD)

        start = page * sources_per_page
        end = start + sources_per_page
        current_sources = sources[start:end]

        y_offset = 2
        for idx, (source_id, source_name) in enumerate(current_sources, start=1):
            line_num = idx
            line_text = f"{idx}. {source_name}"
            safe_addstr(stdscr, y_offset, 2, line_text, curses.color_pair(2) | curses.A_UNDERLINE)
            y_offset += 1

        # Istruzioni per la navigazione
        navigation = "Premi un numero per selezionare la fonte, 'n' per pagina successiva, 'p' per precedente, 'q' per tornare indietro."
        safe_addstr(stdscr, height - 2, 2, navigation, curses.color_pair(3))
        stdscr.refresh()

        key = stdscr.getch()
        if key == ord('q'):
            break
        elif key == ord('n') and page < total_pages - 1:
            page += 1
        elif key == ord('p') and page > 0:
            page -= 1
        elif ord('1') <= key <= ord(str(min(9, len(current_sources)))):
            selection = key - ord('0') - 1
            if 0 <= selection < len(current_sources):
                selected_source_id, selected_source_name = current_sources[selection]
                display_articles_by_source(stdscr, db_name, selected_source_id, selected_source_name)
        else:
            # Mostra un messaggio di errore temporaneo
            show_message(stdscr, "Input non valido! Premi un tasto per continuare.", curses.color_pair(5))

def display_articles_by_source(stdscr, db_name, source_id, source_name):
    """
    Mostra gli articoli di una specifica fonte in modo paginato.
    Limita la visualizzazione a 9 articoli per pagina per facilitare la selezione.
    """
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    c.execute('''
        SELECT id, title, published FROM articles 
        WHERE source_id = ?
        ORDER BY 
            CASE 
                WHEN published IS NOT NULL THEN datetime(published)
                ELSE datetime('1970-01-01')
            END DESC
    ''', (source_id,))
    rows = c.fetchall()
    conn.close()

    articles = []
    for art_id, title, published_str in rows:
        dt = parse_date_str(published_str)  # None se parsing fallisce
        articles.append((art_id, title, published_str, dt))

    # Ordiniamo decrescentemente in base a dt (chi non ha data, va in fondo)
    articles.sort(key=lambda x: x[3] if x[3] else datetime.min, reverse=True)

    page = 0
    articles_per_page = 9  # Limitazione a 9 articoli per pagina
    total_pages = (len(articles) + articles_per_page - 1) // articles_per_page

    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        stdscr.border()
        # Titolo
        title_text = f" Articoli di '{source_name}' (Pagina {page + 1}/{total_pages}) "
        stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
        stdscr.addstr(0, max((width - len(title_text)) // 2, 0), title_text)
        stdscr.attroff(curses.color_pair(6) | curses.A_BOLD)

        start = page * articles_per_page
        end = start + articles_per_page
        current_articles = articles[start:end]

        y_offset = 2
        for idx, (art_id, title, published_str, dt) in enumerate(current_articles, start=1):
            line_num = idx
            line_text = f"{idx}. {title} ({published_str})"
            safe_addstr(stdscr, y_offset, 2, line_text, curses.color_pair(1))
            y_offset += 1

        # Istruzioni per la navigazione
        navigation = "Premi un numero (1-9) per leggere l'articolo, 'n' per pagina successiva, 'p' per precedente, 'q' per tornare indietro."
        safe_addstr(stdscr, height - 2, 2, navigation, curses.color_pair(3))
        stdscr.refresh()

        key = stdscr.getch()
        if key == ord('q'):
            break
        elif key == ord('n') and page < total_pages - 1:
            page += 1
        elif key == ord('p') and page > 0:
            page -= 1
        elif ord('1') <= key <= ord(str(min(9, len(current_articles)))):
            selection = key - ord('0') - 1
            if 0 <= selection < len(current_articles):
                article_id = current_articles[selection][0]
                show_article(stdscr, db_name, article_id)
        else:
            # Mostra un messaggio di errore temporaneo
            show_message(stdscr, "Input non valido! Premi un tasto per continuare.", curses.color_pair(5))

def show_article(stdscr, db_name, article_id):
    """
    Mostra un menù di azioni (visualizza, salva, stampa, modifica tag, torna indietro) per l’articolo selezionato.
    """
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    c.execute('SELECT title, content, published FROM articles WHERE id = ?', (article_id,))
    article = c.fetchone()
    if article:
        title, content, pubdate = article
        c.execute('''
            SELECT tags.tag FROM tags
            JOIN article_tags ON tags.id = article_tags.tag_id
            WHERE article_tags.article_id = ?
        ''', (article_id,))
        tags = [row[0] for row in c.fetchall()]
    conn.close()

    stdscr.clear()
    height, width = stdscr.getmaxyx()
    stdscr.border()
    # Titolo
    safe_addstr(stdscr, 0, 2, title, curses.A_BOLD | curses.color_pair(2))
    # Pubblicazione
    safe_addstr(stdscr, 1, 2, f"Pubblicato: {pubdate}", curses.color_pair(3))
    # Tags
    tags_line = "Tags: " + ", ".join(tags) if tags else "Tags: nessuno"
    safe_addstr(stdscr, 2, 2, tags_line, curses.color_pair(3))
    # Opzioni
    action_options = [
        "1. Visualizza a Schermo",
        "2. Salva su File",
        "3. Stampa",
        "4. Modifica Tags",
        "5. Torna Indietro"
    ]
    start_y = 4
    for idx, option in enumerate(action_options, start=start_y):
        safe_addstr(stdscr, idx, 4, option, curses.color_pair(2))

    # Istruzioni
    instruction = "Seleziona un'opzione premendo il numero corrispondente."
    safe_addstr(stdscr, start_y + len(action_options) + 1, 4, instruction, curses.color_pair(3))
    stdscr.refresh()

    while True:
        key = stdscr.getch()
        if key == ord('1'):
            display_full_article(stdscr, title, content)
            break
        elif key == ord('2'):
            save_article_to_file(stdscr, title, content)
            break
        elif key == ord('3'):
            print_article(stdscr, title, content)
            break
        elif key == ord('4'):
            edit_tags_ui(stdscr, db_name, article_id, tags)
            break
        elif key == ord('5'):
            break
        else:
            show_message(stdscr, "Opzione non valida! Premi un tasto per continuare.", curses.color_pair(5))

def display_full_article(stdscr, title, content):
    """
    Visualizza l'articolo a schermo con una semplice paginazione (max righe visibili).
    Premi 'n' per pagina successiva, 'p' per pagina precedente, 'q' per uscire.
    """
    lines = content.splitlines()
    page = 0
    lines_per_page = curses.LINES - 6  # Spazio per titolo, pubblicazione, tag e footer
    total_pages = (len(lines) + lines_per_page - 1) // lines_per_page

    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        stdscr.border()
        # Titolo
        safe_addstr(stdscr, 0, 2, title, curses.A_BOLD | curses.color_pair(2))
        # Contenuto
        start = page * lines_per_page
        end = start + lines_per_page
        chunk = lines[start:end]

        for idx, line in enumerate(chunk, start=2):
            safe_addstr(stdscr, idx, 2, line)

        # Footer
        footer = f"Pagina {page + 1}/{total_pages} | [n] Avanti, [p] Indietro, [q] Esci"
        safe_addstr(stdscr, lines_per_page + 3, 2, footer, curses.color_pair(3))
        stdscr.refresh()

        key = stdscr.getch()
        if key == ord('q'):
            break
        elif key == ord('n') and page < total_pages - 1:
            page += 1
        elif key == ord('p') and page > 0:
            page -= 1
        else:
            show_message(stdscr, "Input non valido! Premi un tasto per continuare.", curses.color_pair(5))

def save_article_to_file(stdscr, title, content):
    """
    Chiede all'utente il percorso dove salvare il file e scrive il contenuto dell'articolo.
    """
    curses.echo()
    stdscr.clear()
    height, width = stdscr.getmaxyx()
    stdscr.border()
    prompt = "Inserisci il percorso del file (es. /home/pi/articolo.txt): "
    safe_addstr(stdscr, 2, 2, prompt, curses.color_pair(3))
    stdscr.refresh()
    filepath_bytes = stdscr.getstr(3, 2, width - 4)
    curses.noecho()

    try:
        filepath = filepath_bytes.decode('utf-8').strip()
        if not filepath:
            raise ValueError("Percorso del file non può essere vuoto.")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"Title: {title}\n\n{content}")
        success_msg = "Articolo salvato con successo!"
        safe_addstr(stdscr, 5, 2, success_msg, curses.color_pair(2))
        logging.info(f"Article saved to file: {filepath}")
    except Exception as e:
        err_msg = f"Errore nel salvataggio: {e}"
        safe_addstr(stdscr, 5, 2, err_msg, curses.color_pair(5))
        logging.error(f"Error saving article to file: {e}")

    # Istruzioni finali
    final_msg = "Premi un tasto per continuare."
    safe_addstr(stdscr, 7, 2, final_msg, curses.color_pair(3))
    stdscr.refresh()
    stdscr.getch()

def print_article(stdscr, title, content):
    """
    Invia l'articolo alla stampante di rete 'Canon' configurata in CUPS.
    """
    full_content = f"Title: {title}\n\n{content}"
    temp_print_file = "/tmp/temp_article.txt"
    try:
        with open(temp_print_file, 'w', encoding='utf-8') as f:
            f.write(full_content)
        
        # Comando per stampare usando CUPS
        subprocess.run(['lp', '-d', PRINTER_NAME, temp_print_file], check=True)
        logging.info(f"Article sent to printer {PRINTER_NAME}")
        show_message(stdscr, "Articolo inviato alla stampante con successo!", curses.color_pair(2))
    except subprocess.CalledProcessError as e:
        logging.error(f"Error printing article: {e}")
        show_message(stdscr, f"Errore nella stampa: {e}", curses.color_pair(5))
    except Exception as e:
        logging.error(f"Error writing to temp file for printing: {e}")
        show_message(stdscr, f"Errore nella stampa: {e}", curses.color_pair(5))
    finally:
        # Rimuovi il file temporaneo se esiste
        if os.path.exists(temp_print_file):
            os.remove(temp_print_file)

def edit_tags_ui(stdscr, db_name, article_id, current_tags):
    """
    Interfaccia per modificare le tag di un articolo.
    Permette di rimuovere tag esistenti e aggiungerne di nuove.
    """
    curses.echo()
    stdscr.clear()
    height, width = stdscr.getmaxyx()
    stdscr.border()
    prompt_remove = "Tag attuali (separati da virgola). Inserisci quelli da rimuovere (lascia vuoto per nessuna rimozione): "
    safe_addstr(stdscr, 2, 2, prompt_remove, curses.color_pair(3))
    safe_addstr(stdscr, 3, 2, f"{', '.join(current_tags)}", curses.color_pair(1))
    stdscr.refresh()
    remove_input_bytes = stdscr.getstr(4, 2, width - 4)
    curses.noecho()

    tags_to_remove = [tag.strip() for tag in remove_input_bytes.decode('utf-8').split(',') if tag.strip()]
    if tags_to_remove:
        remove_tags(db_name, article_id, tags_to_remove)

    curses.echo()
    stdscr.clear()
    stdscr.border()
    prompt_add = "Inserisci le nuove tag da aggiungere (separati da virgola): "
    safe_addstr(stdscr, 2, 2, prompt_add, curses.color_pair(3))
    stdscr.refresh()
    add_input_bytes = stdscr.getstr(3, 2, width - 4)
    curses.noecho()

    tags_to_add = [tag.strip() for tag in add_input_bytes.decode('utf-8').split(',') if tag.strip()]
    if tags_to_add:
        add_tags(db_name, article_id, tags_to_add)

    # Messaggio di conferma
    success_msg = "Tag aggiornate con successo!"
    safe_addstr(stdscr, 5, 2, success_msg, curses.color_pair(2))
    logging.info(f"Tags updated for article ID {article_id}: Added {tags_to_add}, Removed {tags_to_remove}")
    
    # Istruzioni finali
    final_msg = "Premi un tasto per continuare."
    safe_addstr(stdscr, 7, 2, final_msg, curses.color_pair(3))
    stdscr.refresh()
    stdscr.getch()

def add_tags(db_name, article_id, tags_to_add):
    """
    Aggiunge nuove tag a un articolo.
    """
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    for tag in tags_to_add:
        c.execute('INSERT OR IGNORE INTO tags (tag) VALUES (?)', (tag,))
        # Recupera l'id del tag appena inserito o esistente
        c.execute('SELECT id FROM tags WHERE tag = ?', (tag,))
        tag_row = c.fetchone()
        if tag_row:
            tag_id = tag_row[0]
            try:
                c.execute('INSERT INTO article_tags (article_id, tag_id) VALUES (?, ?)', (article_id, tag_id))
            except sqlite3.IntegrityError:
                pass  # Relazione già esistente
    conn.commit()
    conn.close()

def remove_tags(db_name, article_id, tags_to_remove):
    """
    Rimuove tag da un articolo.
    """
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    for tag in tags_to_remove:
        # Recupera l'id del tag
        c.execute('SELECT id FROM tags WHERE tag = ?', (tag,))
        tag_row = c.fetchone()
        if tag_row:
            tag_id = tag_row[0]
            # Rimuove la relazione
            c.execute('DELETE FROM article_tags WHERE article_id = ? AND tag_id = ?', (article_id, tag_id))
    conn.commit()
    conn.close()

def manage_feeds_ui(stdscr, db_name):
    """
    Interfaccia per gestire le sorgenti RSS.
    Permette di elencare, eliminare e rinominare le sorgenti RSS.
    """
    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        stdscr.border()
        # Titolo
        title_text = " Gestisci Feed RSS "
        stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
        stdscr.addstr(0, max((width - len(title_text)) // 2, 0), title_text)
        stdscr.attroff(curses.color_pair(6) | curses.A_BOLD)

        # Opzioni di gestione
        options = [
            "1. Elenca Tutti i Feed",
            "2. Elimina un Feed",
            "3. Rinomina un Feed",
            "4. Torna al Menu Principale"
        ]
        start_y = 2
        for idx, option in enumerate(options, start=start_y):
            safe_addstr(stdscr, idx, 2, option, curses.color_pair(2) if idx == start_y else 0)

        # Istruzioni
        instruction = "Seleziona un'opzione premendo il numero corrispondente."
        safe_addstr(stdscr, start_y + len(options) + 2, 2, instruction, curses.color_pair(3))
        stdscr.refresh()

        key = stdscr.getch()

        if key == ord('1'):
            list_feeds_ui(stdscr, db_name)
        elif key == ord('2'):
            delete_feed_ui(stdscr, db_name)
        elif key == ord('3'):
            rename_feed_ui(stdscr, db_name)
        elif key == ord('4'):
            break
        else:
            show_message(stdscr, "Opzione non valida! Premi un tasto per continuare.", curses.color_pair(5))

def list_feeds_ui(stdscr, db_name):
    """
    Elenca tutte le sorgenti RSS con nome e URL.
    """
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    c.execute('SELECT id, name, url FROM sources ORDER BY name ASC')
    feeds = c.fetchall()
    conn.close()

    if not feeds:
        show_message(stdscr, "Nessun feed RSS presente.", curses.color_pair(5))
        return

    page = 0
    feeds_per_page = 10  # Numero di feed per pagina
    total_pages = (len(feeds) + feeds_per_page - 1) // feeds_per_page

    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        stdscr.border()
        # Titolo
        title_text = f" Elenco dei Feed RSS (Pagina {page + 1}/{total_pages}) "
        stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
        stdscr.addstr(0, max((width - len(title_text)) // 2, 0), title_text)
        stdscr.attroff(curses.color_pair(6) | curses.A_BOLD)

        start = page * feeds_per_page
        end = start + feeds_per_page
        current_feeds = feeds[start:end]

        y_offset = 2
        for idx, (feed_id, name, url) in enumerate(current_feeds, start=1):
            line_text = f"{start + idx}. Nome: {name} | URL: {url}"
            safe_addstr(stdscr, y_offset, 2, line_text, curses.color_pair(1))
            y_offset += 1

        # Istruzioni per la navigazione
        navigation = "Premi 'n' per pagina successiva, 'p' per precedente, 'q' per tornare indietro."
        safe_addstr(stdscr, height - 2, 2, navigation, curses.color_pair(3))
        stdscr.refresh()

        key = stdscr.getch()
        if key == ord('q'):
            break
        elif key == ord('n') and page < total_pages - 1:
            page += 1
        elif key == ord('p') and page > 0:
            page -= 1
        else:
            show_message(stdscr, "Input non valido! Premi un tasto per continuare.", curses.color_pair(5))

def delete_feed_ui(stdscr, db_name):
    """
    Interfaccia per eliminare un feed RSS.
    """
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    c.execute('SELECT id, name, url FROM sources ORDER BY name ASC')
    feeds = c.fetchall()
    conn.close()

    if not feeds:
        show_message(stdscr, "Nessun feed RSS presente da eliminare.", curses.color_pair(5))
        return

    page = 0
    feeds_per_page = 10  # Numero di feed per pagina
    total_pages = (len(feeds) + feeds_per_page - 1) // feeds_per_page

    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        stdscr.border()
        # Titolo
        title_text = f" Elimina un Feed RSS (Pagina {page + 1}/{total_pages}) "
        stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
        stdscr.addstr(0, max((width - len(title_text)) // 2, 0), title_text)
        stdscr.attroff(curses.color_pair(6) | curses.A_BOLD)

        start = page * feeds_per_page
        end = start + feeds_per_page
        current_feeds = feeds[start:end]

        y_offset = 2
        for idx, (feed_id, name, url) in enumerate(current_feeds, start=1):
            line_text = f"{start + idx}. Nome: {name} | URL: {url}"
            safe_addstr(stdscr, y_offset, 2, line_text, curses.color_pair(1))
            y_offset += 1

        # Istruzioni per la navigazione
        navigation = "Premi il numero del feed da eliminare, 'n' per pagina successiva, 'p' per precedente, 'q' per tornare indietro."
        safe_addstr(stdscr, height - 2, 2, navigation, curses.color_pair(3))
        stdscr.refresh()

        key = stdscr.getch()
        if key == ord('q'):
            break
        elif key == ord('n') and page < total_pages - 1:
            page += 1
        elif key == ord('p') and page > 0:
            page -= 1
        elif ord('1') <= key <= ord(str(min(9, len(current_feeds)))):
            selection = key - ord('0') - 1
            if 0 <= selection < len(current_feeds):
                selected_feed_id, selected_name, selected_url = current_feeds[selection]
                # Conferma eliminazione
                confirm = confirm_action(stdscr, f"Sei sicuro di voler eliminare il feed '{selected_name}'?")
                if confirm:
                    delete_source(db_name, selected_feed_id)
                    delete_feed_from_file(selected_url)
                    show_message(stdscr, f"Feed '{selected_name}' eliminato con successo.", curses.color_pair(2))
                    # Ricarica la lista dei feed dopo l'eliminazione
                    conn = sqlite3.connect(db_name)
                    c = conn.cursor()
                    c.execute('SELECT id, name, url FROM sources ORDER BY name ASC')
                    feeds = c.fetchall()
                    conn.close()
                    if not feeds:
                        show_message(stdscr, "Tutti i feed sono stati eliminati.", curses.color_pair(2))
                        break
                    total_pages = (len(feeds) + feeds_per_page - 1) // feeds_per_page
        else:
            show_message(stdscr, "Input non valido! Premi un tasto per continuare.", curses.color_pair(5))

def rename_feed_ui(stdscr, db_name):
    """
    Interfaccia per rinominare un feed RSS.
    """
    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    c.execute('SELECT id, name, url FROM sources ORDER BY name ASC')
    feeds = c.fetchall()
    conn.close()

    if not feeds:
        show_message(stdscr, "Nessun feed RSS presente da rinominare.", curses.color_pair(5))
        return

    page = 0
    feeds_per_page = 10  # Numero di feed per pagina
    total_pages = (len(feeds) + feeds_per_page - 1) // feeds_per_page

    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        stdscr.border()
        # Titolo
        title_text = f" Rinomina un Feed RSS (Pagina {page + 1}/{total_pages}) "
        stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
        stdscr.addstr(0, max((width - len(title_text)) // 2, 0), title_text)
        stdscr.attroff(curses.color_pair(6) | curses.A_BOLD)

        start = page * feeds_per_page
        end = start + feeds_per_page
        current_feeds = feeds[start:end]

        y_offset = 2
        for idx, (feed_id, name, url) in enumerate(current_feeds, start=1):
            line_text = f"{start + idx}. Nome: {name} | URL: {url}"
            safe_addstr(stdscr, y_offset, 2, line_text, curses.color_pair(1))
            y_offset += 1

        # Istruzioni per la navigazione
        navigation = "Premi il numero del feed da rinominare, 'n' per pagina successiva, 'p' per precedente, 'q' per tornare indietro."
        safe_addstr(stdscr, height - 2, 2, navigation, curses.color_pair(3))
        stdscr.refresh()

        key = stdscr.getch()
        if key == ord('q'):
            break
        elif key == ord('n') and page < total_pages - 1:
            page += 1
        elif key == ord('p') and page > 0:
            page -= 1
        elif ord('1') <= key <= ord(str(min(9, len(current_feeds)))):
            selection = key - ord('0') - 1
            if 0 <= selection < len(current_feeds):
                selected_feed_id, selected_name, selected_url = current_feeds[selection]
                # Chiede il nuovo nome
                new_name = get_user_input(stdscr, "Inserisci il nuovo nome per il feed:", width)
                if new_name:
                    update_source_name(db_name, selected_feed_id, new_name)
                    show_message(stdscr, f"Feed '{selected_name}' rinominato in '{new_name}'.", curses.color_pair(2))
        else:
            show_message(stdscr, "Input non valido! Premi un tasto per continuare.", curses.color_pair(5))

def get_user_input(stdscr, prompt, width):
    """
    Chiede all'utente di inserire una stringa di testo.
    """
    curses.echo()
    stdscr.clear()
    height, _ = stdscr.getmaxyx()
    stdscr.border()
    safe_addstr(stdscr, height//2 - 1, 2, prompt, curses.color_pair(3))
    stdscr.refresh()
    input_bytes = stdscr.getstr(height//2, 2, width - 4)
    curses.noecho()

    try:
        user_input = input_bytes.decode('utf-8').strip()
        return user_input
    except UnicodeDecodeError:
        return ''

def confirm_action(stdscr, message):
    """
    Chiede conferma all'utente per un'azione.
    Restituisce True se confermato, False altrimenti.
    """
    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        stdscr.border()
        safe_addstr(stdscr, height//2 - 1, 2, message + " (y/n): ", curses.color_pair(3))
        stdscr.refresh()
        key = stdscr.getch()
        if key in [ord('y'), ord('Y')]:
            return True
        elif key in [ord('n'), ord('N')]:
            return False

def add_feed_ui(stdscr):
    """
    Interfaccia per aggiungere un nuovo feed RSS.
    """
    curses.echo()
    stdscr.clear()
    height, width = stdscr.getmaxyx()
    stdscr.border()
    prompt = "Inserisci l'URL del nuovo feed RSS: "
    safe_addstr(stdscr, 2, 2, prompt, curses.color_pair(3))
    stdscr.refresh()
    feed_url_bytes = stdscr.getstr(3, 2, width - 4)
    curses.noecho()

    try:
        feed_url = feed_url_bytes.decode('utf-8').strip()
        if not feed_url:
            raise ValueError("L'URL del feed non può essere vuoto.")
        # Tenta di estrarre il titolo del feed
        feed = feedparser.parse(feed_url)
        if feed.bozo:
            raise ValueError(f"Errore nel parsing del feed: {feed.bozo_exception}")
        feed_title = feed.feed.get('title', 'Unnamed Feed')
        source_id = save_source(DB_PATH, feed_title, feed_url)
        add_feed(feed_url)  # Aggiungi al file feeds.txt
        success_msg = f"Feed '{feed_title}' aggiunto con successo!"
        safe_addstr(stdscr, 5, 2, success_msg, curses.color_pair(2))
        logging.info(f"New feed added via UI: {feed_title} ({feed_url})")
    except Exception as e:
        err_msg = f"Errore nell'aggiunta del feed: {e}"
        safe_addstr(stdscr, 5, 2, err_msg, curses.color_pair(5))
        logging.error(f"Error adding feed via UI: {e}")

    # Istruzioni finali
    final_msg = "Premi un tasto per continuare."
    safe_addstr(stdscr, 7, 2, final_msg, curses.color_pair(3))
    stdscr.refresh()
    stdscr.getch()

def archive_articles_ui(stdscr, db_name):
    """
    Interfaccia per archiviare gli articoli obsoleti.
    Mostra una barra di avanzamento durante il processo di archiviazione.
    """
    stdscr.clear()
    height, width = stdscr.getmaxyx()
    progress_win = curses.newwin(7, width - 4, height//2 - 3, 2)
    progress_win.border()
    progress_win.refresh()

    try:
        # Archivia gli articoli
        archive_old_articles(db_name, threshold_days=ARCHIVE_THRESHOLD_DAYS)
        show_message(stdscr, "Archiviazione completata! Premi un tasto per continuare.", curses.color_pair(2))
    except Exception as e:
        logging.error(f"Error during archiving: {e}")
        show_message(stdscr, f"Errore durante l'archiviazione: {e}", curses.color_pair(5))
    finally:
        progress_win.clear()
        progress_win.refresh()

def update_articles_ui(stdscr, db_name):
    """
    Interfaccia per aggiornare gli articoli scaricando eventuali nuovi articoli.
    Mostra una barra di avanzamento e il feed corrente in elaborazione.
    """
    stdscr.clear()
    height, width = stdscr.getmaxyx()
    progress_win = curses.newwin(5, width - 4, height//2 - 2, 2)
    progress_win.border()
    progress_win.refresh()

    try:
        process_feeds(db_name, progress_win)
        show_message(stdscr, "Aggiornamento completato! Premi un tasto per continuare.", curses.color_pair(2))
    except Exception as e:
        logging.error(f"Error during update: {e}")
        show_message(stdscr, f"Errore durante l'aggiornamento: {e}", curses.color_pair(5))
    finally:
        progress_win.clear()
        progress_win.refresh()

def search_ui(stdscr, db_name):
    """
    Interfaccia per cercare articoli in base ai tag.
    Mostra i primi 9 risultati e permette di selezionare uno.
    """
    curses.echo()
    stdscr.clear()
    height, width = stdscr.getmaxyx()
    stdscr.border()
    prompt = "Cerca Articoli per Tag (separati da virgola): "
    safe_addstr(stdscr, 2, 2, prompt, curses.color_pair(3))
    stdscr.refresh()
    search_input_bytes = stdscr.getstr(3, 2, width - 4)
    curses.noecho()

    try:
        search_input = search_input_bytes.decode('utf-8').strip()
    except UnicodeDecodeError:
        search_input = ''

    search_tags = [tag.strip() for tag in search_input.split(',') if tag.strip()]
    results = search_articles(db_name, search_tags)

    if not results:
        stdscr.clear()
        stdscr.border()
        no_result_msg = f"Nessun articolo trovato per i tag: {search_tags}"
        safe_addstr(stdscr, 2, 2, no_result_msg, curses.color_pair(5))
        instruction = "Premi un tasto per tornare indietro."
        safe_addstr(stdscr, 4, 2, instruction, curses.color_pair(3))
        stdscr.refresh()
        stdscr.getch()
        return

    # Paginazione dei risultati
    page = 0
    articles_per_page = 9  # Limitazione a 9 articoli per pagina
    total_pages = (len(results) + articles_per_page - 1) // articles_per_page

    while True:
        stdscr.clear()
        stdscr.border()
        # Titolo dei risultati
        title_text = f" Risultati per {search_tags} (Pagina {page + 1}/{total_pages}) "
        stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
        stdscr.addstr(0, max((width - len(title_text)) // 2, 0), title_text)
        stdscr.attroff(curses.color_pair(6) | curses.A_BOLD)

        start = page * articles_per_page
        end = start + articles_per_page
        current_results = results[start:end]

        y_offset = 2
        for idx, (art_id, title, link, published) in enumerate(current_results, start=1):
            line_num = idx
            line_text = f"{idx}. {title} ({published})"
            safe_addstr(stdscr, y_offset, 2, line_text, curses.color_pair(1))
            y_offset += 1

        # Istruzioni per la navigazione
        navigation = "Premi un numero (1-9) per leggere l'articolo, 'n' per pagina successiva, 'p' per precedente, 'q' per tornare indietro."
        safe_addstr(stdscr, articles_per_page + 3, 2, navigation, curses.color_pair(3))
        stdscr.refresh()

        key = stdscr.getch()
        if key == ord('q'):
            break
        elif key == ord('n') and page < total_pages - 1:
            page += 1
        elif key == ord('p') and page > 0:
            page -= 1
        elif ord('1') <= key <= ord(str(min(9, len(current_results)))):
            selection = key - ord('0') - 1
            if 0 <= selection < len(current_results):
                article_id = current_results[selection][0]
                show_article(stdscr, db_name, article_id)
        else:
            # Mostra un messaggio di errore temporaneo
            show_message(stdscr, "Input non valido! Premi un tasto per continuare.", curses.color_pair(5))

def search_articles(db_name, search_tags):
    """
    Esegue la ricerca degli articoli che abbiano TUTTI i tag passati in input.
    Restituisce una lista di tuple (id, title, link, published).
    """
    if not search_tags:
        return []

    conn = sqlite3.connect(db_name)
    c = conn.cursor()
    placeholders = ','.join(['?'] * len(search_tags))
    
    # Trova l'id di ogni tag cercato
    c.execute(f'SELECT id FROM tags WHERE tag IN ({placeholders})', search_tags)
    tag_ids = [row[0] for row in c.fetchall()]
    
    if not tag_ids:
        conn.close()
        return []
    
    # Cerchiamo gli articoli che hanno TUTTI i tag cercati
    query = f'''
        SELECT articles.id, articles.title, articles.link, articles.published
        FROM articles
        JOIN article_tags ON articles.id = article_tags.article_id
        WHERE article_tags.tag_id IN ({','.join(['?'] * len(tag_ids))})
        GROUP BY articles.id
        HAVING COUNT(DISTINCT article_tags.tag_id) = ?
    '''
    
    c.execute(query, (*tag_ids, len(tag_ids)))
    results = c.fetchall()
    conn.close()
    return results

def show_message(stdscr, message, attr=0):
    """
    Mostra un messaggio temporaneo al centro dello schermo.
    Attende che l'utente prema un tasto per continuare.
    """
    stdscr.clear()
    height, width = stdscr.getmaxyx()
    stdscr.border()
    y = height // 2
    x = max((width - len(message)) // 2, 0)
    safe_addstr(stdscr, y, x, message, attr)
    stdscr.refresh()
    stdscr.getch()

# ----------------------------------------------------------------------------
# SAFE ADDSTR PER EVITARE ERRORI CURSES
# ----------------------------------------------------------------------------

def safe_addstr(stdscr, y, x, text, attr=0):
    """
    Aggiunge la stringa 'text' alla finestra curses tronandola 
    se supera la larghezza disponibile. Evita l'errore "_curses.error: addwstr() returned ERR".
    """
    max_y, max_x = stdscr.getmaxyx()
    if y < 0 or y >= max_y:
        return  # Fuori dallo schermo verticalmente
    truncated_text = text[:max_x - x - 1]  # Tronca se troppo lunga
    try:
        stdscr.addstr(y, x, truncated_text, attr)
    except curses.error:
        pass  # Se ci sono ancora problemi, ignoriamo l'errore

# ----------------------------------------------------------------------------
# FUNZIONE PER L'ARCHIVIAZIONE NON INTERATTIVA
# ----------------------------------------------------------------------------

def perform_archiving(db_name):
    """
    Esegue l'archiviazione degli articoli obsoleti senza interfaccia utente.
    """
    try:
        archive_old_articles(db_name, threshold_days=ARCHIVE_THRESHOLD_DAYS)
        logging.info("Archiviazione completata.")
        print("Archiviazione completata.")
    except Exception as e:
        logging.error(f"Errore durante l'archiviazione: {e}")
        print(f"Errore durante l'archiviazione: {e}")

# ----------------------------------------------------------------------------
# FUNZIONE PER L'AGGIORNAMENTO DELLE FEEDS
# ----------------------------------------------------------------------------

def update_articles_ui(stdscr, db_name):
    """
    Interfaccia per aggiornare gli articoli scaricando eventuali nuovi articoli.
    Mostra una barra di avanzamento e il feed corrente in elaborazione.
    """
    stdscr.clear()
    height, width = stdscr.getmaxyx()
    progress_win = curses.newwin(5, width - 4, height//2 - 2, 2)
    progress_win.border()
    progress_win.refresh()

    try:
        process_feeds(db_name, progress_win)
        show_message(stdscr, "Aggiornamento completato! Premi un tasto per continuare.", curses.color_pair(2))
    except Exception as e:
        logging.error(f"Error during update: {e}")
        show_message(stdscr, f"Errore durante l'aggiornamento: {e}", curses.color_pair(5))
    finally:
        progress_win.clear()
        progress_win.refresh()

# ----------------------------------------------------------------------------
# FUNZIONE PER L'ARCHIVIAZIONE DEGLI ARTICOLI
# ----------------------------------------------------------------------------

def archive_articles_ui(stdscr, db_name):
    """
    Interfaccia per archiviare gli articoli obsoleti.
    Mostra una barra di avanzamento durante il processo di archiviazione.
    """
    stdscr.clear()
    height, width = stdscr.getmaxyx()
    progress_win = curses.newwin(7, width - 4, height//2 - 3, 2)
    progress_win.border()
    progress_win.refresh()

    try:
        # Archivia gli articoli
        archive_old_articles(db_name, threshold_days=ARCHIVE_THRESHOLD_DAYS)
        show_message(stdscr, "Archiviazione completata! Premi un tasto per continuare.", curses.color_pair(2))
    except Exception as e:
        logging.error(f"Error during archiving: {e}")
        show_message(stdscr, f"Errore durante l'archiviazione: {e}", curses.color_pair(5))
    finally:
        progress_win.clear()
        progress_win.refresh()

# ----------------------------------------------------------------------------
# FUNZIONE PER AGGIORNARE LA PROGRESS BAR
# ----------------------------------------------------------------------------

def update_progress_bar(win, current, total, message=""):
    """
    Aggiorna una barra di avanzamento in una finestra curses.

    :param win: La finestra curses dove visualizzare la progress bar
    :param current: L'attuale progresso (numero)
    :param total: Il totale del progresso (numero)
    :param message: Messaggio da mostrare accanto alla barra
    """
    win.clear()
    win.border()
    # Calcola la larghezza della barra di avanzamento
    bar_width = win.getmaxyx()[1] - 4  # Lascia spazio per i bordi
    progress = int((current / total) * bar_width)
    bar = "[" + "#" * progress + "-" * (bar_width - progress) + "]"
    # Mostra la barra di avanzamento
    win.addstr(2, 2, bar)
    # Mostra il messaggio corrente
    win.addstr(3, 2, message)
    win.refresh()

# ----------------------------------------------------------------------------
# FUNZIONE PRINCIPALE
# ----------------------------------------------------------------------------

def main():
    """
    1. Crea le cartelle necessarie (db/, config/, logs/, archive/).
    2. Inizializza il database.
    3. Gestisce le opzioni da riga di comando:
       - --update: Aggiorna gli articoli scaricando nuovi feed senza avviare l'interfaccia utente.
       - --archive: Archivia gli articoli obsoleti senza avviare l'interfaccia utente.
       - Nessuna opzione: Avvia l'interfaccia utente curses.
    """
    parser = argparse.ArgumentParser(description="RSS Archiver: Scarica e gestisci articoli dai feed RSS.")
    parser.add_argument('--update', action='store_true', help='Aggiorna il database scaricando nuovi articoli senza avviare l\'interfaccia utente.')
    parser.add_argument('--archive', action='store_true', help='Archivia gli articoli obsoleti senza avviare l\'interfaccia utente.')
    args = parser.parse_args()

    # Crea le directory se non esistono
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(os.path.dirname(FEEDS_FILE), exist_ok=True)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    # Inizializza il database (crea le tabelle se non esistono)
    initialize_db()

    if args.update and args.archive:
        # Esegui entrambe le operazioni
        perform_archiving(DB_PATH)
        curses.wrapper(update_articles_ui, DB_PATH)
        print("Database aggiornato e articoli archiviati con successo.")
    elif args.update:
        # Avvia l'interfaccia temporanea per mostrare la progress bar
        curses.wrapper(update_articles_ui, DB_PATH)
        print("Database aggiornato con successo.")
    elif args.archive:
        # Esegui l'archiviazione senza interfaccia utente
        perform_archiving(DB_PATH)
    else:
        # Avvia l'interfaccia utente curses
        curses.wrapper(ui_main, DB_PATH)

if __name__ == "__main__":
    main()

