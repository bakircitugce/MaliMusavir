# MaliAsistan — Faz 0

SMMM büroları için çok kiracılı muhasebe otomasyon platformunun temel altyapısı.

## Yerel kurulum

```powershell
uv sync
Copy-Item .env.example .env
# .env içindeki AUTH_SECRET'ı doldur (en az 32 bayt):
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
uv run python -m app.cli secure-db          # mevcut veritabanı varsa (Faz 0 geçişi)
uv run python -m app.cli create-admin --email ad@buro.com --ad-soyad "Ad Soyad" --buro-unvan "Büro Unvanı"
uv run uvicorn app.main:app --reload
```

## Güvenlik modeli (Faz 0)

- `AUTH_SECRET` ortam değişkeni zorunludur; yoksa veya 32 bayttan kısaysa uygulama açılmaz.
- Varsayılan hesap yoktur. İlk yönetici `python -m app.cli create-admin` ile oluşturulur
  (şifre gizli sorulur; en az 12 karakter, büyük/küçük harf ve rakam).
- `/health`, `/auth/login` ve `/app` (statik panel) dışındaki her uç
  `Authorization: Bearer <token>` ister.
- Büro kimliği yalnızca token'dan alınır; `X-Buro-ID` header'ı yok sayılır.
  Mükellef `X-Mukellef-ID` ile seçilir ve her istekte kullanıcının bürosuna ait olup
  olmadığı ve kullanıcının erişimi doğrulanır. Yetkisiz erişim `404` döner.
- Roller: `admin` (kullanıcı yönetimi + tümü), `musavir` (fiş ters kaydı, ERP/e-Fatura
  gönderimi, tüm büro), `personel` (belge/inceleme/mutabakat, yalnızca atandığı
  mükellefler), `mukellef` (atandığı mükellefi salt okuma). Atama:
  `PUT /auth/users/{user_id}/mukellefler/{mukellef_id}` (yalnızca admin).
- Token'lar PyJWT/HS256, varsayılan 8 saat geçerlidir. Çıkış, şifre değişikliği veya
  pasife alma tüm eski token'ları iptal eder.
- 5 başarısız girişte hesap 15 dakika kilitlenir; giriş uçlarında hız sınırı vardır.
- Eski `yonetici/kullanici/stajyer` rolleri `admin/personel/personel` olarak okunur;
  `secure-db` bunları kalıcı olarak dönüştürür ve eski `admin@`/`muhasebe@` demo
  hesaplarını pasife alır.

Sağlık kontrolü: `http://localhost:8000/health`

Kullanıcı dostu onay kuyruğu: `http://localhost:8000/app`

Panel giriş ekranıyla açılır; giriş sonrası kullanıcının erişebildiği ilk mükellef
seçilir. Belirli bir mükellefi açmak için:

```text
http://localhost:8000/app?mukellef_id=<MUKELLEF_UUID>
```

Panel; güven skorunu, sistemin önerdiği yevmiye kaydını ve “Bu kayıt neden
önerildi?” açıklamasını gösterir. İlk görsel sürümde örnek veriler kullanılır;
gerçek belge API'lerine bağlanırken tenant header'ları ve kullanıcı yetkisi
korunmalıdır.

Gerçek panelde bir belge onaylandığında review kaydıyla birlikte öğrenme
geri bildirimi de `/learning/feedback` endpoint'ine gönderilir. Öğrenme
önerileri yine güven eşiği ve tekrar eden insan onaylarıyla sınırlıdır.
Belge detayı açıldığında mevcut öğrenilmiş öneri de açıklama alanında gösterilir.
“Düzelt” seçeneği gerçek belgede kategori ve not kaydeder; bu karar da öğrenme
geri bildirimi olarak saklanır.
“Sonra” seçeneği belgeyi onaylamadan `needs_review` durumunda bırakır.
Tenant bilgileriyle açılan panelde “Belge yükle” düğmesi yeni dosyayı belge
API'sine gönderir ve kuyruk otomatik yenilenir.
Panel yükleme sırasında tekrar tıklamayı engeller ve 25 MB sınırını önceden
kontrol eder.
Yükleme tamamlandığında belge işleme endpoint'i de başlatılır; işleme hatası
kullanıcıya açıkça gösterilir.
Kuyruk kartlarında belgenin işleniyor, hazır veya inceleme gerekli durumu da
gösterilir.

Varsayılan yerel veritabanı SQLite'tır ve `mali_asistan.sqlite3` dosyasında tutulur.
Docker/PostgreSQL kullanmak istersen `.env` içindeki `DATABASE_URL` değerini
`postgresql+asyncpg://...` adresiyle değiştirip PostgreSQL'i ayrıca başlatabilirsin.

Test ve kalite kontrolleri:

```powershell
uv run pytest -q
uv run ruff check .
```

## Faz 0 kapsamı

- FastAPI ve Pydantic ayarları
- PostgreSQL/Redis/MinIO geliştirme servisleri
- Büro, kullanıcı, mükellef ve yürürlük tarihli vergi parametresi modelleri
- Tenant kimliğinin modellerde zorunlu tutulması
- Ekli NACE ve mizan Excel'leri için gerçek kolon düzenine uygun içe aktarma
- Sağlık kontrolü ve temel test altyapısı

## Seed belgeleri

`data/seed/` altında iki yerel kaynak çalışma kitabı kullanılır:

- `sektor_meslek_nace_2026_03.xlsx`: A–F sektör/meslek ilişkili NACE tablosu ve
  G–H bağımsız güncel NACE listesi ayrı ayrı okunur.
- `mizan_gokcem_tav.xlsx`: hiyerarşik hesap kodları, hesap adları ve borç/alacak
  sütunları okunur. Denge kontrolü, hiyerarşik satırları tekrar toplamamak için
  Excel'deki `TOPLAM` satırını esas alır.

Bu dosyalar müşteri verisi içerebileceği için `.gitignore` ile repoya alınmaz.
İçe aktarma fonksiyonları `app/nace/importer.py` ve
`app/chart/import_mizan.py` içindedir.

## Excel import komutu

Veritabanına yazmadan önce iki dosyayı doğrulamak için:

```powershell
uv run python -m app.import_data `
  --nace data/seed/sektor_meslek_nace_2026_03.xlsx `
  --mizan data/seed/mizan_gokcem_tav.xlsx `
  --dry-run
```

Mizanı belirli bir mükellefe aktarmak için mevcut büro ve mükellef UUID'leri
verilmelidir. Yerel deneme için önce bu kayıtları oluşturup UUID'leri kullan:

```powershell
uv run python -m app.bootstrap_demo
uv run python -m app.import_data `
  --mizan data/seed/mizan_gokcem_tav.xlsx `
  --buro-id <BURO_UUID> `
  --mukellef-id <MUKELLEF_UUID>
```

Import idempotent'tir: aynı NACE kaynağı veya mükellef hesap kodu tekrar
aktarıldığında yeni mükerrer kayıt oluşturulmaz; değişen alanlar güncellenir.

## Belge yükleme ve UBL okuma

Yerel geliştirmede belgeler `storage/` altında saklanır. Yükleme endpoint'leri
tenant'ı header'lardan alır:

```powershell
curl.exe -X POST http://localhost:8000/documents/upload `
  -H "Authorization: Bearer <TOKEN>" `
  -H "X-Mukellef-ID: <MUKELLEF_UUID>" `
  -F "upload=@fatura.xml"
```

Aynı tenant içinde aynı SHA-256 içeriğe sahip belge tekrar yüklenirse `409`
döner. UBL-TR Invoice XML'i `app/documents/ubl.py` içindeki parser ile ortak
fatura çıkarım şemasına dönüştürülür.

Yüklenen belgeyi türüne göre işlemek için:

```powershell
curl.exe -X POST http://localhost:8000/documents/<BELGE_UUID>/extract `
  -H "Authorization: Bearer <TOKEN>" `
  -H "X-Mukellef-ID: <MUKELLEF_UUID>"
```

Sonuç `GET /documents/<BELGE_UUID>` yanıtında `extraction_status`,
`extracted_text` ve `extraction_metadata` alanlarında görünür. OCR için ayrıca
Tesseract motoru ve `tur` dil verisi gerekir.

Upload sonrası extraction yerel geliştirme kuyruğuna alınır. Bu kuyruk süreç
içinde çalışır ve kalıcı değildir; üretimde Redis/Celery worker ile
değiştirilmelidir. Kalıcı kuyruk kurulana kadar `/extract` endpoint'i manuel
tekrar çalıştırma için kullanılabilir.

Metin tabanlı PDF'ler `app/documents/pdf.py` ile sayfa sayfa metne çevrilir.
Taranmış PDF'ler bu aşamada boş metin döndürebilir; OCR katmanı sonraki adımda
eklenecektir.

JPG/PNG OCR çekirdeği `app/documents/ocr.py` içinde `tur+eng` diliyle çalışır.
Python paketlerine ek olarak Windows'ta Tesseract OCR motoru ve `tur` dil
verisi kurulmalıdır. Motor bulunamazsa sistem boş sonuç üretmez; açık bir
`OCRDependencyError` verir.

Üretim ortamında `.env` kullanılmalı; gizli bilgiler kaynak koda yazılmamalıdır.
