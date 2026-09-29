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
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

# Sayfa çekerken yakalanacak hatalar
FETCH_ERRORS = (requests.RequestException,)

# CloudFlare bypass
try:
    import cloudscraper
    from cloudscraper.exceptions import CloudflareException
    HAS_CLOUDSCRAPER = True
    # Çözülemeyen challenge'da cloudscraper RequestException değil kendi
    # hatasını fırlatıyor; yakalanmazsa HTML yedeğine düşmeden script çöküyor.
    FETCH_ERRORS += (CloudflareException,)
except ImportError:
    HAS_CLOUDSCRAPER = False


def create_session():
    """CloudFlare korumasını geçebilen session oluştur."""
    if HAS_CLOUDSCRAPER:
        print("[BİLGİ] cloudscraper kullanılıyor (CloudFlare bypass).")
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False}
        )
        # Brotli sıkıştırmayı devre dışı bırak — decode sorunu önlenir
        scraper.headers["Accept-Encoding"] = "gzip, deflate"
        return scraper
    else:
        print("[BİLGİ] requests kullanılıyor (cloudscraper yüklü değil).")
        session = requests.Session()
        session.headers.update(HEADERS)
        session.headers["Accept-Encoding"] = "gzip, deflate"
        return session


# ── Ayarlar ──────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
# Actions'ta workflow bunu "state" dalındaki kopyaya yönlendiriyor (SEEN_FILE);
# main'e her tur bot commit'i düşmesin. Lokalde script'in yanındaki dosya.
SEEN_FILE = os.environ.get("SEEN_FILE") or os.path.join(os.path.dirname(__file__), "seen_topics.json")

FORUM_URL = "https://forum.donanimarsivi.com/forumlar/Sicakfirsatlar/"
# XenForo RSS feed - yedek. Forum şu an misafire 403 + giriş sayfası dönüyor,
# açılırsa diye tutuluyor. Konu etiketini (🔥İndirim vb.) taşımıyor.
RSS_URL = "https://forum.donanimarsivi.com/forumlar/Sicakfirsatlar/index.rss"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
    "Referer": "https://forum.donanimarsivi.com/",
}

# Kaç sayfa taransın (1 sayfa = 15 konu). RSS yedeğinde uygulanmaz, feed'in
# boyutunu forum belirliyor.
PAGES_TO_SCAN = 2

# Kaç konu ID'si hatırlansın (günde ~30 yeni konu → ~2 hafta)
SEEN_LIMIT = 500

# Tur başına en fazla kaç bildirim gönderilsin. Fazlası görüldü işaretlenmez,
# sonraki turda gider; CallMeBot limiti tek turda dolup gerçek bildirimler düşmesin.
MAX_NOTIFICATIONS_PER_RUN = 10

# Sırlar yalnızca ortam değişkeninden (GitHub Secrets) okunuyor. Eski kurulumlarda
# config.json'da kalmış olabilirler; uyarmak için isimleri tutuluyor.
LEGACY_SECRET_KEYS = ("callmebot_phone", "callmebot_apikey", "telegram_bot_token", "telegram_chat_id")

# Debug çıktıları varsayılan olarak kapalı. DEBUG=true ile ya da Actions'ta
# "Re-run jobs → Enable debug logging" ile (RUNNER_DEBUG=1) açılır.
DEBUG = (
    os.environ.get("DEBUG", "").strip().lower() in ("1", "true", "yes")
    or os.environ.get("RUNNER_DEBUG") == "1"
)


def debug(msg):
    if DEBUG:
        print(f"[DEBUG] {msg}")


def load_config():
    """config.json dosyasından ayarları yükle."""
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def newest_ids(ids):
    """
    En yeni SEEN_LIMIT konu ID'si, artan sırada.
    Eklenme sırasına göre değil ID'ye göre kırpılıyor: forum konuları son
    mesaja göre sıraladığı için yeni yanıt alan eski konular listeye geri
    düşüyor ve eklenme sırasıyla kırpınca yeni konuları dışarı itiyorlardı.
    """
    return sorted({str(i) for i in ids if str(i).isdigit()}, key=int)[-SEEN_LIMIT:]


def load_seen():
    """Daha önce görülmüş konu ID'lerini yükle."""
    if not os.path.exists(SEEN_FILE):
        return []
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        content = f.read().strip()
    # README kurulumda dosyayı boş bırakmayı söylüyor, boş dosya json'u patlatmasın
    return newest_ids(json.loads(content)) if content else []


def save_seen(seen_list):
    """Görülmüş konu ID'lerini kaydet."""
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(newest_ids(seen_list), f)


def fetch_topics(pages=PAGES_TO_SCAN):
    """
    Sıcak Fırsatlar'daki konuları çek.
    Önce HTML sayfalarını tarar: konu etiketi yalnızca orada var ("İndirim
    Bitti" filtresi ve bildirimdeki etiket buna dayanıyor). Sonuç çıkmazsa
    RSS feed'e düşer.
    """
    session = create_session()

    # Yöntem 1: HTML Scraping (tercih edilen)
    topics = fetch_via_html(session, pages)
    if topics:
        return topics

    # Yöntem 2: RSS Feed (yedek)
    print("[BİLGİ] HTML taramasından konu çıkmadı, RSS feed deneniyor...")
    return fetch_via_rss(session)


def fetch_via_rss(session):
    """
    XenForo'nun RSS feed'ini kullan (yedek).
    Feed konu etiketini taşımıyor (category alanı forum adı), bu yolda
    "bitti" kontrolü yalnızca başlıktan yapılabiliyor.
    """
    print(f"[TARAMA] RSS feed: {RSS_URL}")

    try:
        resp = session.get(RSS_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except FETCH_ERRORS as e:
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
        if "bitti" in normalize_tr(title):
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
    HTML scraping yöntemi (birincil).
    Sıcak Fırsatlar sayfalarını tara, konu başlıklarını, linklerini ve
    etiketlerini döndür.
    """
    topics = []

    for page_num in range(1, pages + 1):
        if page_num > 1:
            time.sleep(1)

        url = FORUM_URL if page_num == 1 else f"{FORUM_URL}page-{page_num}"
        print(f"[TARAMA] Sayfa {page_num}: {url}")

        try:
            resp = session.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except FETCH_ERRORS as e:
            print(f"[HATA] Sayfa alınamadı: {e}")
            continue

        debug(f"Sayfa boyutu: {len(resp.text)} karakter")
        debug(f"İlk 500 karakter:\n{resp.text[:500]}")
        debug("─────────────────────────────")

        soup = BeautifulSoup(resp.text, "html.parser")

        # Yalnızca ana içerik sütunu. Kenar çubuğundaki "son konular" bloğu
        # (li.block-row) başka forumların konularını listeliyor, üst menüde de
        # /konu/ linkleri var; sayfanın tamamı taranınca fırsat diye bildiriliyorlardı.
        content = soup.select_one("div.p-body-content")
        if content is None:
            print(f"[HATA] Sayfa {page_num}: ana içerik bulunamadı (Cloudflare sayfası ya da tema değişikliği).")
            continue

        thread_items = content.select("div.structItem")
        debug(f"div.structItem ile {len(thread_items)} öğe bulundu")

        if not thread_items:
            # Debug: içerikteki tüm linkleri kontrol et
            all_links = content.find_all("a", href=True)
            konu_links = [a for a in all_links if "/konu/" in a.get("href", "")]
            debug(f"Sayfadaki toplam link: {len(all_links)}, /konu/ içeren: {len(konu_links)}")

            # Doğrudan konu linklerinden topic çek
            for a in konu_links:
                title = a.get_text(strip=True)
                href = a.get("href", "")

                if not title or len(title) < 5:
                    continue
                if "bitti" in normalize_tr(title):
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

            debug(f"Link taramasından {len(topics)} konu eklendi")
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

            prefix_el = item.select_one("div.structItem-title span.label")
            prefix = prefix_el.get_text(strip=True) if prefix_el else ""

            if "bitti" in normalize_tr(prefix) or "bitti" in normalize_tr(title):
                continue

            topics.append({
                "id": topic_id,
                "title": title,
                "url": full_url,
                "prefix": prefix,
            })

    return topics


def extract_topic_id(href):
    """
    URL'den konu ID'sini çıkar: /konu/baslik.123456/ → 123456
    Slug'sız /konu/123456/ biçimini de tanır. Konu linki değilse None döner.
    """
    match = re.search(r"/konu/(?:[^/?#]*\.)?(\d+)(?:[/?#]|$)", href)
    return match.group(1) if match else None


TR_TO_ASCII = str.maketrans("ıöüşçğ", "iouscg")


def normalize_tr(text):
    """
    Karşılaştırma için küçült ve Türkçe karakterleri sadeleştir.
    "İ" önce elle eşleniyor: str.lower() onu "i" + birleşen nokta (U+0307)
    yapıyor ve "İNDİRİM BİTTİ" içinde "bitti" bulunamıyor.
    """
    return text.replace("İ", "i").lower().translate(TR_TO_ASCII)


def match_keywords(title, keywords):
    """
    Başlıktaki kelimeleri kontrol et.
    Her keyword grubu için OR mantığı, gruplar arası AND değil.
    Herhangi bir keyword eşleşirse True döner.

    Keyword kelime başında aranır: "RAM" → "RAM'li" eşleşir, "Program" /
    "Telegram" eşleşmez; "DDR5" → "LPDDR5", "2TB" → "12TB" eşleşmez.
    Sonu serbest: "Corsair RM" gibi önekler "Corsair RM850x"i yakalasın.
    """
    title_normalized = normalize_tr(title)

    for kw in keywords:
        if re.search(r"(?<!\w)" + re.escape(normalize_tr(kw)), title_normalized):
            return True, kw
    return False, None


def redact(text, *secrets):
    """
    Loga basılacak metinden sırları çıkar. Actions yalnızca Secret'ın birebir
    değerini maskeliyor; URL-encode edilmiş ya da "+" eklenmiş hali açık kalıyor.
    Uzun olan önce: kısa bir sır uzun olanın içindeyse onu bölmesin.
    """
    for secret in sorted(filter(None, secrets), key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


def send_whatsapp(phone, apikey, message):
    """CallMeBot API ile WhatsApp mesajı gönder."""
    # Tüm parametreler encode ediliyor: "+905..." gibi bir numaradaki "+"
    # ham bırakılınca sunucuda boşluğa dönüşüyordu.
    query = urlencode({"phone": phone, "text": message, "apikey": apikey}, quote_via=quote)
    url = f"https://api.callmebot.com/whatsapp.php?{query}"
    secrets = (phone, re.sub(r"\D", "", phone), apikey)

    print("[WHATSAPP] Mesaj gönderiliyor...")

    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            print("[WHATSAPP] ✅ Mesaj gönderildi!")
            return True
        else:
            # Hata gövdesi HTML: önce numarayı ve mesajın tamamını yansıtıyor,
            # sebep ("APIKey is invalid" vb.) en sonda. Baştan kırpınca sebep
            # kayboluyor, numara loga düşüyordu.
            body = " ".join(re.sub(r"<[^>]+>", " ", resp.text).split())
            print(f"[WHATSAPP] ❌ Hata: {resp.status_code} - {redact(body, *secrets)[-200:]}")
            return False
    except requests.RequestException as e:
        # Bağlantı hatalarının mesajı istek URL'sini (numara + apikey) içeriyor
        print(f"[WHATSAPP] ❌ Bağlantı hatası: {redact(str(e), *secrets)}")
        return False


def send_telegram(bot_token, chat_id, message):
    """Telegram Bot API ile mesaj gönder (yedek kanal)."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    # Düz metin: mesajda HTML yok, parse_mode=HTML ile başlıktaki "<" veya "&"
    # Telegram'da "can't parse entities" (400) hatasına yol açıyordu.
    data = {"chat_id": chat_id, "text": message}

    try:
        resp = requests.post(url, data=data, timeout=30)
        if resp.status_code == 200:
            print("[TELEGRAM] ✅ Mesaj gönderildi!")
            return True
        else:
            print(f"[TELEGRAM] ❌ Hata: {resp.status_code} - {resp.text[:200]}")
            return False
    except requests.RequestException as e:
        # Bağlantı hatalarının mesajı token'lı URL'yi içeriyor
        print(f"[TELEGRAM] ❌ Bağlantı hatası: {redact(str(e), bot_token)}")
        return False


def format_notification(topic, matched_keyword, bold="*"):
    """
    Bildirim mesajını formatla.
    bold: başlığı kalın yapan işaret. WhatsApp "*" kullanıyor; Telegram'a düz
    metin gidiyor (parse_mode yok), orada yıldızlar olduğu gibi görünüyordu.
    """
    now = datetime.now(timezone.utc).strftime("%H:%M %d/%m/%Y")
    prefix_text = f"[{topic['prefix']}] " if topic["prefix"] else ""

    msg = (
        f"🔥 {bold}FIRSAT ALARMI!{bold} 🔥\n"
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
    # Boş keyword her başlıkla eşleşir ve her konu için bildirim atar
    keywords = [kw for kw in config.get("keywords", []) if kw.strip()]

    # Sırlar yalnızca ortam değişkeninden: config.json public repoda duruyor
    # ve oradan gelen değer Actions'ın secret maskelemesine girmiyor.
    # strip: Secret'a yapıştırırken sona kaçan satır sonu "APIKey is invalid" yapıyor.
    phone = os.environ.get("CALLMEBOT_PHONE", "").strip()
    apikey = os.environ.get("CALLMEBOT_APIKEY", "").strip()

    # Telegram yedek kanal (opsiyonel)
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    has_whatsapp = bool(phone and apikey)
    has_telegram = bool(tg_token and tg_chat)

    legacy = [k for k in LEGACY_SECRET_KEYS if config.get(k)]
    if legacy:
        print(f"[UYARI] config.json'daki {', '.join(legacy)} artık okunmuyor. "
              "Ortam değişkeni / GitHub Secret olarak tanımla ve config.json'dan sil.")

    # Çiftin yarısı tanımlıysa büyük ihtimalle Secret adı yanlış yazıldı
    if bool(phone) != bool(apikey):
        print("[UYARI] CALLMEBOT_PHONE ve CALLMEBOT_APIKEY'den biri eksik, WhatsApp kanalı kapalı.")
    if bool(tg_token) != bool(tg_chat):
        print("[UYARI] TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID'den biri eksik, Telegram kanalı kapalı.")

    if not keywords:
        print("[HATA] Anahtar kelime listesi boş! config.json'u kontrol et.")
        return 1

    if not has_whatsapp and not has_telegram:
        # Actions'ta kanalsız tur konuları görüldü işaretleyip kimseye haber
        # vermeden yeşil biterdi; eşleşmeler sessizce kaybolurdu.
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print("[HATA] Bildirim kanalı tanımlı değil. GitHub Secrets'ı kontrol et (README adım 4).")
            return 1
        print("[UYARI] Bildirim kanalı tanımlı değil (CallMeBot/Telegram). Sadece konsola yazılacak.")
    elif not has_whatsapp:
        print("[BİLGİ] CallMeBot bilgileri eksik, sadece Telegram kullanılacak.")

    print(f"[BİLGİ] {len(keywords)} anahtar kelime yüklendi.")
    print(f"[BİLGİ] Kelimeler: {', '.join(keywords[:10])}...")

    # ── Daha önce görülenleri yükle ──
    seen_ids = load_seen()
    print(f"[BİLGİ] {len(seen_ids)} konu daha önce görülmüş.")
    # Liste dolunca eski ID'ler düşüyor. Hatırladığımız en eski konudan da eski
    # olanlar yeni yanıt alıp öne çıkmış eski konulardır; zaten bildirildiler.
    oldest_seen = min(map(int, seen_ids)) if len(seen_ids) >= SEEN_LIMIT else 0
    # Liste boşsa (yeni kurulum, sıfırlanan dosya) forumdaki her konu yeni
    # görünür ve her eşleşmeye ayrı mesaj gider. Bu tur sadece işaretlenir.
    bootstrap = not seen_ids
    if bootstrap:
        print("[BİLGİ] İlk çalıştırma: mevcut konular görüldü olarak işaretlenecek, bildirim gönderilmeyecek.")

    # ── Forumu tara ──
    topics = fetch_topics()
    print(f"[BİLGİ] {len(topics)} aktif konu bulundu.")

    if not topics:
        # Forumda her zaman konu var; hiç gelmediyse RSS de HTML de engellendi
        # ya da site yapısı değişti. Run kırmızı olsun ki sessizce körleşmeyelim.
        print("[HATA] Hiç konu alınamadı, tarama başarısız.")
        return 1

    # ── Eşleştirme ve bildirim ──
    new_matches = 0
    failed = 0
    deferred = 0
    silenced = 0
    # Tarama sırasında yeni mesaj alan konu sayfa 1'den 2'ye kayıp iki kez
    # gelebiliyor; gönderilemeyen ya da ertelenen konu seen'e girmediği için
    # ikinci kopyası tekrar denenir ve sayaçları şişirirdi.
    handled = set()

    for topic in topics:
        # ID'siz linkleri takip edemeyiz; görülmüş ya da eski konuları atla
        if (not topic["id"] or topic["id"] in handled or topic["id"] in seen_ids
                or int(topic["id"]) < oldest_seen):
            continue
        handled.add(topic["id"])

        matched, keyword = match_keywords(topic["title"], keywords)

        if matched and bootstrap:
            silenced += 1
        elif matched:
            if new_matches >= MAX_NOTIFICATIONS_PER_RUN:
                # Görüldü işaretleme, sonraki turda gönderilsin
                deferred += 1
                continue

            new_matches += 1
            print(f"\n[EŞLEŞTİ] 🎯 {topic['title']}")
            print(f"          Kelime: {keyword}")
            print(f"          Link: {topic['url']}")

            results = []

            # WhatsApp gönder
            if has_whatsapp:
                results.append(send_whatsapp(phone, apikey, format_notification(topic, keyword)))

            # Telegram yedek. Düz metin kalıyor: MarkdownV2'de başlıktaki "*"
            # ya da "_" mesajın reddedilmesine yol açar.
            if has_telegram:
                results.append(send_telegram(tg_token, tg_chat, format_notification(topic, keyword, bold="")))

            # Rate limit: CallMeBot için, Telegram da aynı sohbete art arda
            # gelen mesajlarda 429 dönüyor. Yalnızca WhatsApp'ta beklemek yetmiyordu.
            if results:
                time.sleep(3)

            # Kanal tanımlı ama hiçbirinden gidemediyse görüldü işaretleme,
            # sonraki çalıştırmada tekrar denensin. Kanal yoksa konsol çıktısı yeterli.
            if results and not any(results):
                failed += 1
                print("[UYARI] Bildirim hiçbir kanaldan gönderilemedi, sonraki taramada tekrar denenecek.")
                continue

        # Görüldü olarak işaretle (eşleşmeyenler dahil)
        seen_ids.append(topic["id"])

    # ── Sonuçları kaydet ──
    save_seen(seen_ids)

    print(f"\n{'=' * 60}")
    if bootstrap:
        print(f"  İlk çalıştırma: {len(seen_ids)} konu işaretlendi, "
              f"{silenced} eşleşme bildirilmedi.")
    else:
        print(f"  Sonuç: {new_matches} yeni eşleşme bulundu.")
    if failed:
        print(f"  {failed} bildirim gönderilemedi, tekrar denenecek.")
    if deferred:
        print(f"  Tur sınırı ({MAX_NOTIFICATIONS_PER_RUN}) doldu, "
              f"{deferred} eşleşme sonraki taramaya kaldı.")
    print(f"{'=' * 60}")

    # Geçersiz/iptal edilmiş API key'de her tur yeşil biter ve kimse fark
    # etmezdi. Run kırmızı olsun; seen yine de kaydedildi, workflow commit'liyor.
    return 1 if failed else 0


if __name__ == "__main__":
    # Windows'ta çıktı dosyaya/pipe'a yönlenince cp1254 kullanılıyor ve ilk
    # emojide UnicodeEncodeError ile çöküyordu.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
