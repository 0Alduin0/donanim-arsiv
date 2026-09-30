"""
tracker.py'nin ağa ihtiyaç duymayan fonksiyonları için testler.
Çalıştırmak için: pytest -q
"""

import json
import os

import pytest
import requests

import tracker
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


# ── Dayanıklılık: tekrar deneme, art arda hata sayacı, dosyalar ─────

PAGE_HTML = (
    '<div class="p-body-content"><div class="structItem">'
    '<div class="structItem-title"><a href="/konu/rtx-5070-indirim.123456/">RTX 5070 indirim</a></div>'
    '</div></div>'
)


class FakeResponse:
    def __init__(self, status=200, text=""):
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")


class FakeSession:
    """Sırayla verilen yanıtları döndürür; Exception verilirse fırlatır."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []
        self.cookies = requests.cookies.RequestsCookieJar()

    def get(self, url, **kwargs):
        self.urls.append(url)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def no_sleep(monkeypatch):
    waits = []
    monkeypatch.setattr(tracker.time, "sleep", waits.append)
    return waits


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker, "SEEN_FILE", str(tmp_path / "seen_topics.json"))
    monkeypatch.setattr(tracker, "STATUS_FILE", str(tmp_path / "scan_status.json"))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    return tmp_path


def test_sayfa_403_sonra_gelir(no_sleep):
    session = FakeSession([FakeResponse(403), requests.ConnectionError("koptu"), FakeResponse(200, PAGE_HTML)])
    content = tracker.fetch_page(session, "https://x/")
    assert content is not None and content.select("div.structItem")
    assert len(session.urls) == 3
    assert no_sleep == list(tracker.FETCH_RETRY_WAITS)


def test_sayfa_cloudflare_sayfasi_tekrar_denenir(no_sleep):
    challenge = FakeResponse(200, "<html><title>Just a moment...</title></html>")
    session = FakeSession([challenge, FakeResponse(200, PAGE_HTML)])
    assert tracker.fetch_page(session, "https://x/") is not None
    assert len(session.urls) == 2


def test_sayfa_denemeler_tukenir(no_sleep):
    session = FakeSession([FakeResponse(403)] * tracker.FETCH_ATTEMPTS)
    assert tracker.fetch_page(session, "https://x/") is None
    assert len(session.urls) == tracker.FETCH_ATTEMPTS


def test_sayfa_beklenmeyen_hata_coker_etmez(no_sleep):
    # cloudscraper çözemediği challenge'da RequestException dışı hata fırlatıyor
    session = FakeSession([ValueError("challenge"), FakeResponse(200, PAGE_HTML)])
    assert tracker.fetch_page(session, "https://x/") is not None


def test_ilk_sayfa_alinamazsa_sonrakiler_atlanir(no_sleep):
    session = FakeSession([FakeResponse(403)] * tracker.FETCH_ATTEMPTS)
    assert tracker.fetch_via_html(session, pages=2) == []
    assert all(url == tracker.FORUM_URL for url in session.urls)


def test_html_konulari_ayiklanir(no_sleep):
    session = FakeSession([FakeResponse(200, PAGE_HTML)])
    topics = tracker.fetch_via_html(session, pages=1)
    assert topics == [{"id": "123456", "title": "RTX 5070 indirim",
                       "url": "https://forum.donanimarsivi.com/konu/rtx-5070-indirim.123456/", "prefix": ""}]


def test_art_arda_hata_esigi(state_dir):
    for n in range(1, tracker.FAIL_AFTER_RUNS):
        assert tracker.finish_run("sorun") == 0
        assert tracker.load_failures() == n
    assert tracker.finish_run("sorun") == 1
    assert tracker.finish_run("sorun") == 1
    # Sorunsuz tur sayacı sıfırlar
    assert tracker.finish_run() == 0
    assert tracker.load_failures() == 0
    assert tracker.finish_run("sorun") == 0


def test_bozuk_dosyalar_cokertmez(state_dir):
    (state_dir / "seen_topics.json").write_text('["1", "2"', encoding="utf-8")
    (state_dir / "scan_status.json").write_text("{bozuk", encoding="utf-8")
    assert tracker.load_seen() == []
    assert tracker.load_failures() == 0
    (state_dir / "seen_topics.json").write_text('{"a": 1}', encoding="utf-8")
    assert tracker.load_seen() == []
    (state_dir / "seen_topics.json").write_text("", encoding="utf-8")
    assert tracker.load_seen() == []


def test_kayit_gecici_dosya_birakmaz(state_dir):
    tracker.save_seen(["3", "1", "2"])
    assert json.loads((state_dir / "seen_topics.json").read_text()) == ["1", "2", "3"]
    assert os.listdir(state_dir) == ["seen_topics.json"]


def test_bildirim_gecici_hatada_tekrar_dener(no_sleep):
    calls = []

    def once():
        calls.append(1)
        return (len(calls) == 2), True

    assert tracker.send_with_retry("TEST", once) is True
    assert len(calls) == 2 and no_sleep == [tracker.NOTIFY_RETRY_WAIT]


def test_bildirim_kalici_hatada_tekrar_denemez(no_sleep):
    calls = []

    def once():
        calls.append(1)
        return False, False

    assert tracker.send_with_retry("TEST", once) is False
    assert len(calls) == 1 and no_sleep == []


def test_whatsapp_baglanti_hatasi_sonra_gider(monkeypatch, no_sleep):
    responses = [requests.ConnectionError("https://api.callmebot.com/...apikey=gizli"), FakeResponse(200)]

    def fake_get(url, timeout):
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(tracker.requests, "get", fake_get)
    assert tracker.send_whatsapp("905551234567", "gizli", "mesaj") is True
    assert responses == []


def test_whatsapp_gecersiz_key_tekrar_denenmez(monkeypatch, no_sleep):
    calls = []
    monkeypatch.setattr(tracker.requests, "get",
                        lambda url, timeout: calls.append(1) or FakeResponse(203, "APIKey is invalid"))
    assert tracker.send_whatsapp("905551234567", "gizli", "mesaj") is False
    assert len(calls) == 1


def _run_main(monkeypatch, topics, sender=None):
    monkeypatch.setattr(tracker, "fetch_topics", lambda: topics)
    for var in ("CALLMEBOT_PHONE", "CALLMEBOT_APIKEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(var, raising=False)
    if sender:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
        monkeypatch.setattr(tracker, "send_telegram", sender)
    return tracker.main()


TOPIC_NEW = {"id": "999999", "title": "RTX 5070 indirim", "url": "https://x/konu/a.999999/", "prefix": ""}


def test_konu_alinamayan_tur_ancak_esikte_kirmizi(monkeypatch, state_dir, no_sleep):
    (state_dir / "seen_topics.json").write_text('["1"]', encoding="utf-8")
    codes = [_run_main(monkeypatch, []) for _ in range(tracker.FAIL_AFTER_RUNS)]
    assert codes == [0] * (tracker.FAIL_AFTER_RUNS - 1) + [1]
    # Seen'e dokunulmadı
    assert json.loads((state_dir / "seen_topics.json").read_text()) == ["1"]


def test_gonderilemeyen_bildirim_seen_e_girmez(monkeypatch, state_dir, no_sleep):
    (state_dir / "seen_topics.json").write_text('["1"]', encoding="utf-8")
    assert _run_main(monkeypatch, [TOPIC_NEW], sender=lambda *a: False) == 0
    assert "999999" not in tracker.load_seen()
    assert tracker.load_failures() == 1
    # Sonraki tur gider, sayaç sıfırlanır
    assert _run_main(monkeypatch, [TOPIC_NEW], sender=lambda *a: True) == 0
    assert "999999" in tracker.load_seen()
    assert tracker.load_failures() == 0


def test_sure_siniri_asilinca_bildirim_ertelenir(monkeypatch, state_dir, no_sleep):
    (state_dir / "seen_topics.json").write_text('["1"]', encoding="utf-8")
    monkeypatch.setattr(tracker, "RUN_TIME_BUDGET", -1)
    sent = []
    assert _run_main(monkeypatch, [TOPIC_NEW], sender=lambda *a: sent.append(a) or True) == 0
    assert sent == []
    assert "999999" not in tracker.load_seen()
    assert tracker.load_failures() == 0
