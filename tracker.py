"""
Donanım Arşivi - Sıcak Fırsatlar Takipçisi
============================================
"Sıcak Fırsatlar" bölümündeki yeni konuları tarar,
belirlenen parça/anahtar kelimelere göre eşleşme bulursa
CallMeBot üzerinden WhatsApp bildirimi gönderir.

GitHub Actions üzerinde cron ile çalışır (ücretsiz).
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

# CloudFlare bypass
try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False


def create_session():
    """CloudFlare korumasını geçebilen session oluştur."""
    if HAS_CLOUDSCRAPER:
        print("[BİLGİ] cloudscraper kullanılıyor (CloudFlare bypass).")
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        return scraper
    else:
        print("[BİLGİ] requests kullanılıyor (cloudscraper yüklü değil).")
        session = requests.Session()
        session.headers.update(HEADERS)
        return session


# ── Ayarlar ──────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
SEEN_FILE = os.path.join(os.path.dirname(__file__), "seen_topics.json")

FORUM_URL = "https://forum.donanimarsivi.com/forumlar/Sicakfirsatlar/"
# XenForo RSS feed - CloudFlare'ı bypass eder, daha güvenilir
RSS_URL = "https://forum.donanimarsivi.com/forumlar/Sicakfirsatlar/index.rss"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://forum.donanimarsivi.com/",
}

# Kaç sayfa taransın (1 sayfa ≈ 20 konu) - HTML yöntemi için
PAGES_TO_SCAN = 2


def load_config():
    """config.json dosyasından ayarları yükle."""
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_seen():
    """Daha önce görülmüş konu ID'lerini yükle."""
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_seen(seen_list):
    """Görülmüş konu ID'lerini kaydet. Son 500 tanesini tut."""
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen_list[-500:], f)


def fetch_topics(pages=PAGES_TO_SCAN):
    """
    Sıcak Fırsatlar'daki konuları çek.
    Önce RSS feed dener (CloudFlare bypass), 
    başarısız olursa HTML scraping'e düşer.
    """
    session = create_session()

    # Yöntem 1: RSS Feed (tercih edilen)
    topics = fetch_via_rss(session)
    if topics:
        return topics

    # Yöntem 2: HTML Scraping (yedek)
    print("[BİLGİ] RSS başarısız, HTML scraping deneniyor...")
    return fetch_via_html(session, pages)


def fetch_via_rss(session):
    """XenForo'nun RSS feed'ini kullan."""
    print(f"[TARAMA] RSS feed: {RSS_URL}")

    try:
        resp = session.get(RSS_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[HATA] RSS alınamadı: {e}")
        return []

    soup = BeautifulSoup(resp.text, "xml")
    items = soup.find_all("item")

    if not items:
        # XML parser yoksa html.parser ile dene
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.find_all("item")

    topics = []
    for item in items:
        title_el = item.find("title")
        link_el = item.find("link")

        if not title_el or not link_el:
            continue

        title = title_el.get_text(strip=True)
        url = link_el.get_text(strip=True)
        # Bazı RSS'lerde link next sibling text olarak gelir
        if not url and link_el.next_sibling:
            url = str(link_el.next_sibling).strip()

        topic_id = extract_topic_id(url)

        # "İndirim Bitti" olanları atla
        if "bitti" in title.lower():
            continue

        topics.append({
            "id": topic_id,
            "title": title,
            "url": url,
            "prefix": "",
        })

    print(f"[BİLGİ] RSS'den {len(topics)} konu alındı.")
    return topics


def fetch_via_html(session, pages=PAGES_TO_SCAN):
    """
    HTML scraping yöntemi (yedek).
    Sıcak Fırsatlar sayfalarını tara, konu başlıklarını ve linklerini döndür.
    """
    topics = []

    for page_num in range(1, pages + 1):
        url = FORUM_URL if page_num == 1 else f"{FORUM_URL}page-{page_num}"
        print(f"[TARAMA] Sayfa {page_num}: {url}")

        try:
            resp = session.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[HATA] Sayfa alınamadı: {e}")
            continue

        print(f"[DEBUG] Sayfa boyutu: {len(resp.text)} karakter")

        soup = BeautifulSoup(resp.text, "html.parser")

        # Birden fazla selector dene - XenForo versiyonuna göre değişebilir
        thread_items = soup.select("div.structItem")
        print(f"[DEBUG] div.structItem ile {len(thread_items)} öğe bulundu")

        if not thread_items:
            # Alternatif selectorler dene
            thread_items = soup.select("li.block-row") or soup.select("div.structItem--thread")
            print(f"[DEBUG] Alternatif selector ile {len(thread_items)} öğe bulundu")

        if not thread_items:
            # Debug: sayfadaki tüm linkleri kontrol et
            all_links = soup.find_all("a", href=True)
            konu_links = [a for a in all_links if "/konu/" in a.get("href", "")]
            print(f"[DEBUG] Sayfadaki toplam link: {len(all_links)}, /konu/ içeren: {len(konu_links)}")

            # Doğrudan konu linklerinden topic çek
            for a in konu_links:
                title = a.get_text(strip=True)
                href = a.get("href", "")

                if not title or len(title) < 5:
                    continue
                if "bitti" in title.lower():
                    continue

                if href.startswith("/"):
                    full_url = f"https://forum.donanimarsivi.com{href}"
                elif href.startswith("http"):
                    full_url = href
                else:
                    full_url = f"https://forum.donanimarsivi.com/{href}"

                topic_id = extract_topic_id(href)

                # Duplicate kontrolü
                if any(t["id"] == topic_id for t in topics):
                    continue

                topics.append({
                    "id": topic_id,
                    "title": title,
                    "url": full_url,
                    "prefix": "",
                })

            print(f"[DEBUG] Link taramasından {len(topics)} konu eklendi")
            continue

        for item in thread_items:
            title_link = item.select_one("div.structItem-title a[href*='/konu/']")
            if not title_link:
                # Alternatif selector
                title_link = item.select_one("a[href*='/konu/']")
            if not title_link:
                continue

            title = title_link.get_text(strip=True)
            href = title_link.get("href", "")

            if href.startswith("/"):
                full_url = f"https://forum.donanimarsivi.com{href}"
            elif href.startswith("http"):
                full_url = href
            else:
                full_url = f"https://forum.donanimarsivi.com/{href}"

            topic_id = extract_topic_id(href)

            prefix_el = item.select_one("span.label")
            prefix = prefix_el.get_text(strip=True) if prefix_el else ""

            if "bitti" in prefix.lower() or "bitti" in title.lower():
                continue

            topics.append({
                "id": topic_id,
                "title": title,
                "url": full_url,
                "prefix": prefix,
            })

        time.sleep(1)

    return topics


def extract_topic_id(href):
    """URL'den konu ID'sini çıkar: /konu/baslik.123456/ → 123456"""
    match = re.search(r"\.(\d+)/?", href)
    return match.group(1) if match else href


def match_keywords(title, keywords):
    """
    Başlıktaki kelimeleri kontrol et.
    Her keyword grubu için OR mantığı, gruplar arası AND değil.
    Herhangi bir keyword eşleşirse True döner.
    """
    title_lower = title.lower()
    # Türkçe karakter normalizasyonu
    title_normalized = (
        title_lower
        .replace("ı", "i")
        .replace("ö", "o")
        .replace("ü", "u")
        .replace("ş", "s")
        .replace("ç", "c")
        .replace("ğ", "g")
    )

    for kw in keywords:
        kw_lower = kw.lower()
        kw_normalized = (
            kw_lower
            .replace("ı", "i")
            .replace("ö", "o")
            .replace("ü", "u")
            .replace("ş", "s")
            .replace("ç", "c")
            .replace("ğ", "g")
        )
        if kw_normalized in title_normalized or kw_lower in title_lower:
            return True, kw
    return False, None


def send_whatsapp(phone, apikey, message):
    """CallMeBot API ile WhatsApp mesajı gönder."""
    encoded_msg = quote(message)
    url = (
        f"https://api.callmebot.com/whatsapp.php"
        f"?phone={phone}&text={encoded_msg}&apikey={apikey}"
    )

    print(f"[WHATSAPP] Mesaj gönderiliyor: {phone}")

    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            print("[WHATSAPP] ✅ Mesaj gönderildi!")
            return True
        else:
            print(f"[WHATSAPP] ❌ Hata: {resp.status_code} - {resp.text[:200]}")
            return False
    except requests.RequestException as e:
        print(f"[WHATSAPP] ❌ Bağlantı hatası: {e}")
        return False


def send_telegram(bot_token, chat_id, message):
    """Telegram Bot API ile mesaj gönder (yedek kanal)."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}

    try:
        resp = requests.post(url, data=data, timeout=30)
        if resp.status_code == 200:
            print("[TELEGRAM] ✅ Mesaj gönderildi!")
            return True
        else:
            print(f"[TELEGRAM] ❌ Hata: {resp.status_code}")
            return False
    except requests.RequestException as e:
        print(f"[TELEGRAM] ❌ Bağlantı hatası: {e}")
        return False


def format_notification(topic, matched_keyword):
    """Bildirim mesajını formatla."""
    now = datetime.now(timezone.utc).strftime("%H:%M %d/%m/%Y")
    prefix_text = f"[{topic['prefix']}] " if topic["prefix"] else ""

    msg = (
        f"🔥 *FIRSAT ALARMI!* 🔥\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{prefix_text}{topic['title']}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔑 Eşleşen: {matched_keyword}\n"
        f"🔗 {topic['url']}\n"
        f"⏰ {now} UTC"
    )
    return msg


def main():
    print("=" * 60)
    print("  Donanım Arşivi - Sıcak Fırsatlar Takipçisi")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60)

    # ── Ayarları yükle ──
    config = load_config()
    keywords = config.get("keywords", [])
    phone = config.get("callmebot_phone", "") or os.environ.get("CALLMEBOT_PHONE", "")
    apikey = config.get("callmebot_apikey", "") or os.environ.get("CALLMEBOT_APIKEY", "")

    # Telegram yedek kanal (opsiyonel)
    tg_token = config.get("telegram_bot_token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat = config.get("telegram_chat_id", "") or os.environ.get("TELEGRAM_CHAT_ID", "")

    if not keywords:
        print("[HATA] Anahtar kelime listesi boş! config.json'u kontrol et.")
        return

    if not phone or not apikey:
        print("[UYARI] CallMeBot bilgileri eksik. Sadece konsola yazılacak.")

    print(f"[BİLGİ] {len(keywords)} anahtar kelime yüklendi.")
    print(f"[BİLGİ] Kelimeler: {', '.join(keywords[:10])}...")

    # ── Daha önce görülenleri yükle ──
    seen_ids = load_seen()
    print(f"[BİLGİ] {len(seen_ids)} konu daha önce görülmüş.")

    # ── Forumu tara ──
    topics = fetch_topics()
    print(f"[BİLGİ] {len(topics)} aktif konu bulundu.")

    # ── Eşleştirme ve bildirim ──
    new_matches = 0

    for topic in topics:
        # Daha önce gördüysek atla
        if topic["id"] in seen_ids:
            continue

        matched, keyword = match_keywords(topic["title"], keywords)

        if matched:
            new_matches += 1
            print(f"\n[EŞLEŞTİ] 🎯 {topic['title']}")
            print(f"          Kelime: {keyword}")
            print(f"          Link: {topic['url']}")

            msg = format_notification(topic, keyword)

            # WhatsApp gönder
            if phone and apikey:
                send_whatsapp(phone, apikey, msg)
                time.sleep(3)  # CallMeBot rate limit

            # Telegram yedek
            if tg_token and tg_chat:
                send_telegram(tg_token, tg_chat, msg)

        # Görüldü olarak işaretle (eşleşsin eşleşmesin)
        seen_ids.append(topic["id"])

    # ── Sonuçları kaydet ──
    save_seen(seen_ids)

    print(f"\n{'=' * 60}")
    print(f"  Sonuç: {new_matches} yeni eşleşme bulundu.")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
