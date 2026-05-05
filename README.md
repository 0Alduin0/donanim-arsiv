# 🔥 Donanım Arşivi - Sıcak Fırsatlar Takipçisi

[forum.donanimarsivi.com](https://forum.donanimarsivi.com) sitesinin **Sıcak Fırsatlar** bölümünü otomatik tarayıp, belirlediğin parça/modele uygun indirim bulunduğunda **WhatsApp'tan bildirim** gönderen bot.

**Tamamen ücretsiz** çalışır: GitHub Actions (sunucu) + CallMeBot (WhatsApp).

---

## 🏗️ Nasıl Çalışır?

```
Her 10 dakikada bir:
  1. "Sıcak Fırsatlar" sayfasını tarar (ilk 2 sayfa)
  2. Konu başlıklarını config.json'daki anahtar kelimelerle karşılaştırır
  3. "İndirim Bitti" etiketli konuları otomatik atlar
  4. Yeni eşleşme bulursa WhatsApp'tan bildirim gönderir
  5. Tekrar bildirim göndermemek için görülen konuları kaydeder
```

---

## 🚀 Kurulum (Adım Adım)

### 1️⃣ CallMeBot API Key Al (2 dakika)

CallMeBot, ücretsiz WhatsApp mesajı göndermeyi sağlayan bir servis.

1. Telefonunda **WhatsApp** aç
2. `+34 644 47 47 69` numarasını rehberine ekle (isim: CallMeBot)
3. Bu numaraya şu mesajı gönder: `I allow callmebot to send me messages`
4. Birkaç saniye içinde sana bir **API key** gönderecek
5. Bu key'i ve telefon numaranı (ülke kodu ile, örn: `905551234567`) not et

### 2️⃣ GitHub Repo Oluştur

1. [github.com](https://github.com) hesabın yoksa aç (ücretsiz)
2. Sağ üstten **"New repository"** tıkla
3. İsim: `da-firsat-tracker` (veya istediğin bir isim)
4. **Private** seç (bilgilerin gizli kalsın)
5. "Create repository" tıkla

### 3️⃣ Dosyaları Yükle

Bu projedeki tüm dosyaları repo'na yükle:
```
da-firsat-tracker/
├── .github/
│   └── workflows/
│       └── tracker.yml        ← GitHub Actions (zamanlayıcı)
├── tracker.py                 ← Ana script
├── config.json                ← Anahtar kelimeler
├── seen_topics.json           ← Görülmüş konular (boş bırak)
├── requirements.txt           ← Python kütüphaneleri
├── .gitignore
└── README.md
```

**Yükleme yöntemi:**
- GitHub web arayüzünden "Upload files" ile sürükle-bırak yapabilirsin
- Veya bilgisayarında git kuruluysa:
```bash
git clone https://github.com/KULLANICI_ADIN/da-firsat-tracker.git
cd da-firsat-tracker
# dosyaları kopyala
git add .
git commit -m "ilk kurulum"
git push
```

### 4️⃣ GitHub Secrets Ekle (Çok Önemli!)

Telefon numaran ve API key'in repo'da açık durmasın. GitHub Secrets kullan:

1. Repo sayfanda **Settings** → **Secrets and variables** → **Actions** git
2. **"New repository secret"** tıkla ve şunları ekle:

| Secret Adı | Değer | Örnek |
|---|---|---|
| `CALLMEBOT_PHONE` | Telefon numaran (ülke kodlu) | `905551234567` |
| `CALLMEBOT_APIKEY` | CallMeBot'un gönderdiği key | `1234567` |

### 5️⃣ GitHub Actions'ı Aktifle

1. Repo sayfanda **Actions** sekmesine git
2. "I understand my workflows..." butonuna tıkla
3. Sol tarafta **"Fırsat Takipçisi"** workflow'unu gör
4. **"Run workflow"** ile manuel test yap

✅ Her şey doğruysa 10 dakikada bir otomatik çalışmaya başlar!

---

## ⚙️ Anahtar Kelimeleri Düzenleme

`config.json` dosyasındaki `keywords` listesini düzenle:

```json
{
  "keywords": [
    "RTX 5070",
    "Ryzen 7 9800X3D",
    "DDR5",
    "SSD",
    "PSU"
  ]
}
```

**İpuçları:**
- Marka + model yazarsan daha isabetli olur: `"RTX 5070"` > `"ekran kartı"`
- Genel kelimeler çok fazla bildirim gönderebilir, dikkatli ol
- Büyük/küçük harf ve Türkçe karakter fark etmez (otomatik normalize eder)

---

## 📱 Bildirim Örneği

WhatsApp'ına şöyle bir mesaj gelecek:

```
🔥 *FIRSAT ALARMI!* 🔥
━━━━━━━━━━━━━━━
[🔥İndirim] MSI RTX 5070 Gaming X - 38.990 TL Trendyol
━━━━━━━━━━━━━━━
🔑 Eşleşen: RTX 5070
🔗 https://forum.donanimarsivi.com/konu/msi-rtx-5070...
⏰ 14:30 05/05/2026 UTC
```

---

## 🔧 Sık Sorulan Sorular

**S: GitHub Actions ücretsiz mi?**
Evet! Public repo'larda sınırsız, private repo'larda ayda 2000 dakika ücretsiz. Bu bot ayda ~4500 dakika kullanır, bu yüzden **repo'yu public yapmak** daha güvenli. (Secrets gizli kalır, endişelenme.)

**S: CallMeBot çalışmazsa?**
CallMeBot bazen yavaş olabiliyor. Yedek olarak Telegram da ekleyebilirsin:
1. Telegram'da `@BotFather`'a `/newbot` yaz, bir bot oluştur
2. Bot token'ını al
3. Botuna bir mesaj gönder, sonra `https://api.telegram.org/botTOKEN/getUpdates` ile chat_id'ni bul
4. GitHub Secrets'a `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` ekle

**S: Tarama sıklığını değiştirebilir miyim?**
`.github/workflows/tracker.yml` dosyasındaki cron satırını düzenle:
- `*/10 * * * *` → Her 10 dakika
- `*/15 * * * *` → Her 15 dakika
- `*/5 * * * *` → Her 5 dakika (daha hızlı ama daha fazla Actions dakikası yer)

**S: Çok fazla bildirim geliyor!**
`config.json`'daki genel kelimeleri (SSD, RAM, GPU gibi) kaldır, sadece spesifik model numaraları bırak.

---

## 📝 Lisans

Kişisel kullanım için serbesttir.
