"""
tracker.py'nin ağa ihtiyaç duymayan fonksiyonları için testler.
Çalıştırmak için: pytest -q
"""

from tracker import (
    SEEN_LIMIT,
    extract_topic_id,
    format_notification,
    match_keywords,
    newest_ids,
    normalize_tr,
)


# ── normalize_tr ─────────────────────────────────────────────────────

def test_buyuk_i():
    # str.lower() "İ"yi "i" + birleşen nokta yapıyordu, "bitti" bulunamıyordu
    assert "bitti" in normalize_tr("İNDİRİM BİTTİ")


def test_turkce_karakterler():
    assert normalize_tr("ŞARJ ÇĞÖÜ Işık") == "sarj cgou isik"


# ── match_keywords ───────────────────────────────────────────────────

def test_kelime_basi():
    assert match_keywords("32GB RAM'li kit", ["RAM"])[0]
    assert not match_keywords("Telegram botu", ["RAM"])[0]
    assert not match_keywords("Program indirimi", ["RAM"])[0]
    assert not match_keywords("LPDDR5 bellek", ["DDR5"])[0]
    assert not match_keywords("12TB HDD", ["2TB"])[0]


def test_kelime_sonu_serbest():
    assert match_keywords("Corsair RM850x 850W", ["Corsair RM"])[0]


def test_buyuk_kucuk_ve_turkce_fark_etmez():
    assert match_keywords("İNDİRİMLİ ekran kartı", ["indirimli"])[0]
    assert match_keywords("msi rtx 5070 gaming", ["RTX 5070"])[0]


def test_eslesen_kelime_donuyor():
    assert match_keywords("MSI RTX 5070 Gaming X", ["SSD", "RTX 5070"]) == (True, "RTX 5070")
    assert match_keywords("Klavye indirimi", ["SSD"]) == (False, None)


# ── extract_topic_id ─────────────────────────────────────────────────

def test_konu_id():
    assert extract_topic_id("/konu/rtx-5070.123456/") == "123456"
    assert extract_topic_id("/konu/123456/") == "123456"
    assert extract_topic_id("https://forum.donanimarsivi.com/konu/rtx-5070.123456/") == "123456"
    assert extract_topic_id("/konu/rtx-5070.123456/page-2") == "123456"
    assert extract_topic_id("/konu/rtx-5070.123456") == "123456"
    assert extract_topic_id("/forumlar/x/") is None


# ── newest_ids ───────────────────────────────────────────────────────

def test_newest_ids_sayisal_siralama():
    # Metin sıralamasında "9" > "10" olurdu
    assert newest_ids(["10", "9", 100]) == ["9", "10", "100"]


def test_newest_ids_tekrar_ve_gecersiz():
    assert newest_ids(["5", "5", 5, None, "abc", ""]) == ["5"]


def test_newest_ids_en_yenileri_tutar():
    ids = [str(i) for i in range(SEEN_LIMIT + 50, 0, -1)]
    result = newest_ids(ids)
    assert len(result) == SEEN_LIMIT
    assert result[0] == "51"
    assert result[-1] == str(SEEN_LIMIT + 50)


# ── format_notification ──────────────────────────────────────────────

TOPIC = {"id": "1", "title": "RTX 5070 *kampanya*", "url": "https://x/konu/a.1/", "prefix": "🔥İndirim"}


def test_bildirim_whatsapp_kalin():
    msg = format_notification(TOPIC, "RTX 5070")
    assert msg.startswith("🔥 *FIRSAT ALARMI!* 🔥")
    assert "[🔥İndirim] RTX 5070 *kampanya*" in msg


def test_bildirim_telegram_duz_metin():
    msg = format_notification(TOPIC, "RTX 5070", bold="")
    assert msg.startswith("🔥 FIRSAT ALARMI! 🔥")
    # Başlıktaki yıldızlara dokunulmuyor
    assert "RTX 5070 *kampanya*" in msg


def test_bildirim_etiketsiz():
    msg = format_notification(dict(TOPIC, prefix=""), "RTX 5070")
    assert "\nRTX 5070 *kampanya*\n" in msg
