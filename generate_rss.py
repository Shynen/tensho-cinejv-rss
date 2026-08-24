import feedparser
import html
import json
import re
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin

STATE_FILE = Path("discord-state.json")

import requests


FEEDS = {
    "cinema": {
        "url": "https://www.allocine.fr/rss/news-cine.xml",
        "title": "Cinéma",
        "discord": True,
        "special_latest": "allocine",
        "latest_page": "https://www.allocine.fr/news/cinema/",
    },
    "jeux-video": {
        "url": "https://www.jeuxvideo.com/rss/rss-news.xml",
        "title": "Jeux vidéo",
        "discord": True,
    },
}


MAX_ITEMS = 10

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/rss+xml, application/xml, text/xml, "
        "text/html, application/xhtml+xml, */*"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def clean_text(value):
    if not value:
        return ""

    return html.unescape(str(value)).strip()


def get_entry_date(entry):
    parsed = (
        entry.get("published_parsed")
        or entry.get("updated_parsed")
    )

    if not parsed:
        return None

    try:
        return datetime(
            *parsed[:6],
            tzinfo=timezone.utc
        )
    except Exception:
        return None


def get_description(entry):
    description = (
        entry.get("summary")
        or entry.get("description")
        or ""
    )

    return clean_text(description)


def fetch_feed(url):
    print("   🌐 Requête HTTP...")

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30,
        allow_redirects=True,
    )

    print(f"   📡 HTTP {response.status_code}")
    print(f"   📍 URL finale : {response.url}")
    print(f"   📦 Taille : {len(response.content)} octets")

    response.raise_for_status()

    feed = feedparser.parse(response.content)

    if feed.bozo:
        print(
            f"   ⚠️ RSS parser warning : "
            f"{feed.bozo_exception}"
        )

    if not feed.entries:
        raise RuntimeError(
            "Le flux RSS ne contient aucun article."
        )

    return feed


def prepare_entries(feed):
    dated_entries = []
    undated_entries = []

    for index, entry in enumerate(feed.entries):
        date = get_entry_date(entry)

        if date is None:
            undated_entries.append(
                (index, entry)
            )
        else:
            dated_entries.append(
                (date, index, entry)
            )

    dated_entries.sort(
        key=lambda item: item[0],
        reverse=True
    )

    entries = (
        [entry for _, _, entry in dated_entries]
        + [entry for _, entry in undated_entries]
    )

    print("   📅 Tri chronologique effectué.")

    for entry in entries[:3]:
        date = get_entry_date(entry)

        title = clean_text(
            entry.get(
                "title",
                "Sans titre"
            )
        )

        print(
            f"   📰 {date} — {title}"
        )

    return entries


def normalize_url(url):
    return (
        url
        .strip()
        .rstrip("/")
        .replace("&amp;", "&")
    )


def find_rss_entry_by_url(entries, target_url):
    target_url = normalize_url(target_url)

    for entry in entries:
        entry_url = normalize_url(
            entry.get("link", "")
        )

        if entry_url == target_url:
            return entry

    return None


def parse_date_value(value):
    if not value:
        return None

    value = value.strip()

    # ISO 8601
    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return parsed.astimezone(timezone.utc)

    except Exception:
        pass

    # Formats RSS classiques
    from email.utils import parsedate_to_datetime

    try:
        parsed = parsedate_to_datetime(value)

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return parsed.astimezone(timezone.utc)

    except Exception:
        return None


def extract_date_from_article_page(soup):
    # 1. Meta article:published_time
    meta = soup.find(
        "meta",
        attrs={
            "property": "article:published_time"
        }
    )

    if meta and meta.get("content"):
        date = parse_date_value(
            meta["content"]
        )

        if date:
            return date

    # 2. itemprop=datePublished
    element = soup.find(
        attrs={
            "itemprop": "datePublished"
        }
    )

    if element:
        value = (
            element.get("content")
            or element.get("datetime")
            or element.get_text(
                " ",
                strip=True
            )
        )

        date = parse_date_value(value)

        if date:
            return date

    # 3. <time datetime="...">
    time_element = soup.find(
        "time",
        attrs={
            "datetime": True
        }
    )

    if time_element:
        date = parse_date_value(
            time_element["datetime"]
        )

        if date:
            return date

    # 4. JSON-LD
    for script in soup.find_all(
        "script",
        attrs={
            "type": "application/ld+json"
        }
    ):
        try:
            data = json.loads(
                script.string or script.get_text()
            )
        except Exception:
            continue

        objects = (
            data
            if isinstance(data, list)
            else [data]
        )

        for obj in objects:
            if not isinstance(obj, dict):
                continue

            value = (
                obj.get("datePublished")
                or obj.get("dateCreated")
            )

            date = parse_date_value(value)

            if date:
                return date

    return None


def fetch_allocine_latest(rss_entries):
    page_url = (
        "https://www.allocine.fr/news/cinema/"
    )

    print()
    print(
        "   🎬 Recherche du dernier article "
        "AlloCiné sur la page officielle..."
    )

    response = requests.get(
        page_url,
        headers=HEADERS,
        timeout=30,
        allow_redirects=True,
    )

    print(
        f"   📡 Page AlloCiné HTTP "
        f"{response.status_code}"
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.content,
        "html.parser"
    )

    # AlloCiné place les articles dans des h2.
    # On garde un fallback plus large au cas où leur HTML évolue.
    article_link = soup.select_one(
        'h2 a[href*="/article/"]'
    )

    if not article_link:
        article_link = soup.select_one(
            'a[href*="/article/fichearticle"]'
        )

    if not article_link:
        raise RuntimeError(
            "Impossible de trouver le premier "
            "article AlloCiné."
        )

    title = clean_text(
        article_link.get_text(
            " ",
            strip=True
        )
    )

    link = urljoin(
        page_url,
        article_link.get("href", "")
    )

    if not title or not link:
        raise RuntimeError(
            "Article AlloCiné trouvé mais "
            "titre ou lien invalide."
        )

    print(
        f"   🎯 Dernier article AlloCiné : "
        f"{title}"
    )

    print(
        f"   🔗 {link}"
    )

    # ---------------------------------------------------------
    # Priorité 1 : si l'article est déjà présent dans le RSS,
    # on utilise sa vraie date RSS.
    # ---------------------------------------------------------
    rss_entry = find_rss_entry_by_url(
        rss_entries,
        link
    )

    if rss_entry:
        date = get_entry_date(
            rss_entry
        )

        if date:
            print(
                "   📅 Article présent dans le RSS "
                f"→ date RSS : {date}"
            )

            return {
                "title": title,
                "link": link,
                "guid": (
                    rss_entry.get("id")
                    or rss_entry.get("guid")
                    or link
                ),
                "description": get_description(
                    rss_entry
                ),
                "date": date,
            }

    # ---------------------------------------------------------
    # Priorité 2 : l'article est sur la page mais pas encore
    # dans le RSS → on ouvre directement l'article.
    # ---------------------------------------------------------
    print(
        "   ⚠️ Article absent du RSS."
    )

    print(
        "   🌐 Lecture de la page de l'article "
        "pour récupérer sa date..."
    )

    article_response = requests.get(
        link,
        headers=HEADERS,
        timeout=30,
        allow_redirects=True,
    )

    print(
        f"   📡 Article HTTP "
        f"{article_response.status_code}"
    )

    article_response.raise_for_status()

    article_soup = BeautifulSoup(
        article_response.content,
        "html.parser"
    )

    date = extract_date_from_article_page(
        article_soup
    )

    if date is None:
        raise RuntimeError(
            "Impossible de récupérer la date "
            "de publication de l'article AlloCiné."
        )

    print(
        f"   📅 Date de l'article : {date}"
    )

    # Description depuis meta description
    description = ""

    meta_description = article_soup.find(
        "meta",
        attrs={
            "name": "description"
        }
    )

    if (
        meta_description
        and meta_description.get("content")
    ):
        description = clean_text(
            meta_description["content"]
        )

    return {
        "title": title,
        "link": link,
        "guid": link,
        "description": description,
        "date": date,
    }


def add_item(channel, entry):
    item = ET.SubElement(
        channel,
        "item"
    )

    title = clean_text(
        entry.get(
            "title",
            "Sans titre"
        )
    )

    link = entry.get(
        "link",
        ""
    ).strip()

    guid = (
        entry.get("id")
        or entry.get("guid")
        or link
    )

    description = get_description(
        entry
    )

    pub_date = get_entry_date(
        entry
    )

    ET.SubElement(
        item,
        "title"
    ).text = title

    ET.SubElement(
        item,
        "link"
    ).text = link

    guid_element = ET.SubElement(
        item,
        "guid",
        {
            "isPermaLink": "false"
        }
    )

    guid_element.text = guid

    ET.SubElement(
        item,
        "description"
    ).text = description

    if pub_date is not None:
        ET.SubElement(
            item,
            "pubDate"
        ).text = format_datetime(
            pub_date
        )


def add_special_item(
    channel,
    article
):
    item = ET.SubElement(
        channel,
        "item"
    )

    ET.SubElement(
        item,
        "title"
    ).text = article["title"]

    ET.SubElement(
        item,
        "link"
    ).text = article["link"]

    guid = ET.SubElement(
        item,
        "guid",
        {
            "isPermaLink": "false"
        }
    )

    guid.text = article["guid"]

    ET.SubElement(
        item,
        "description"
    ).text = article["description"]

    ET.SubElement(
        item,
        "pubDate"
    ).text = format_datetime(
        article["date"]
    )



def entry_to_article(entry):
    link = entry.get("link", "").strip()
    return {
        "title": clean_text(entry.get("title", "Sans titre")),
        "link": link,
        "guid": entry.get("id") or entry.get("guid") or link,
        "description": get_description(entry),
        "date": get_entry_date(entry),
    }


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        print("   ⚠️ État Discord illisible, réinitialisation.")
        return {}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def update_discord_feed(
    category,
    config,
    article,
    state,
):
    if article is None:
        print("   ⏭️ Aucun article à publier.")
        return False

    guid = article["guid"]
    previous_guid = state.get(category)

    if previous_guid == guid:
        print(
            f"   ⏭️ Aucun nouvel article "
            f"{config['title']} depuis le dernier run."
        )
        return False

    create_feed(
        config,
        [],
        filename=f"{category}-discord.xml",
        max_items=1,
        special_article=article,
    )

    state[category] = guid

    print(
        f"   🔔 Nouvel article {config['title']} : "
        f"{article['title']}"
    )

    return True

def create_feed(
    config,
    entries,
    filename,
    max_items=MAX_ITEMS,
    special_article=None
):
    rss = ET.Element(
        "rss",
        {
            "version": "2.0"
        }
    )

    channel = ET.SubElement(
        rss,
        "channel"
    )

    ET.SubElement(
        channel,
        "title"
    ).text = (
        f"Actualités - "
        f"{config['title']}"
    )

    ET.SubElement(
        channel,
        "link"
    ).text = config["url"]

    ET.SubElement(
        channel,
        "description"
    ).text = (
        f"Flux RSS "
        f"{config['title']} - Tensho"
    )

    if special_article:
        add_special_item(
            channel,
            special_article
        )

        count = 1

    else:
        for entry in entries[:max_items]:
            add_item(
                channel,
                entry
            )

        count = min(
            len(entries),
            max_items
        )

    tree = ET.ElementTree(
        rss
    )

    ET.indent(
        tree,
        space=" "
    )

    output = Path(
        filename
    )

    tree.write(
        output,
        encoding="UTF-8",
        xml_declaration=True
    )

    print(
        f"   🟢 {output} généré "
        f"({count} article(s))."
    )


def main():
    print("========================================")
    print("Tensho Ciné & Jeux vidéo RSS")
    print("========================================")

    state = load_state()
    state_changed = False
    successful = 0
    failed = 0

    for category, config in FEEDS.items():
        print()
        print(f"🔎 Récupération : {config['title']}")
        print(f"   {config['url']}")

        try:
            feed = fetch_feed(config["url"])
            entries = prepare_entries(feed)

            print(f"   📰 {len(entries)} articles récupérés.")

            # Flux normal : 10 derniers articles.
            create_feed(
                config,
                entries,
                filename=f"{category}.xml",
                max_items=MAX_ITEMS,
            )

            # Flux Discord : seulement lorsqu'un nouvel article apparaît.
            if config.get("special_latest") == "allocine":
                article = fetch_allocine_latest(entries)
            else:
                article = entry_to_article(entries[0])

            if config["discord"]:
                changed = update_discord_feed(
                    category,
                    config,
                    article,
                    state,
                )
                state_changed = state_changed or changed

            successful += 1

        except Exception as error:
            print(f"   ❌ Échec : {error}")
            failed += 1

    if state_changed:
        save_state(state)
        print("   💾 État Discord sauvegardé.")
    else:
        print("   ℹ️ Aucun changement Discord.")

    print()
    print("========================================")
    print(f"RSS TERMINÉ — {successful} OK / {failed} échec(s)")
    print("========================================")


if __name__ == "__main__":
    main()
