# Part 3 Tasarımı (v3): PeriodService ve Üç Tarih Modeli

Durum: **Tasarım onaylandı (v3). Uygulama başlamadı.** Part 3a uygulama planı onay bekliyor
(bkz. `part3a-uygulama-plani.md`).

## 1. Üç tarih

| Kavram | Sütun | Kural |
|---|---|---|
| Belge tarihi (`document_date`) | `belgeler.document_date` (+ değişmez ilk okuma `document_date_extracted`) | Yalnızca taslakta, denetim kayıtlı düzeltilir; belgeye bağlı onaylı fiş varsa değişmez |
| Muhasebeleştirme tarihi (`posting_date`) | Mevcut `yevmiye_fisleri.entry_date`; ORM'de `posting_date = synonym("entry_date")`. **İkinci sütun yok.** | Açık hesap dönemi + açık ay içinde olmalı; onayda kesinleşir |
| Vergiyi doğuran olay tarihi (`tax_point_date`) | `belgeler.tax_point_date` | **Yalnızca müşavir/admin doğrulamasıyla** yazılır; doğrulanmamışsa NULL |

Tek doğruluk kaynağı: kaynak belgeli fiş belge bilgisini belgeden okur; fişin kendi belge alanları
yalnızca belgesiz fişlerde doludur (DB CHECK).

## 2. Tax Point

- Kanıt yoksa: `tax_point_date = NULL`, `tax_point_basis = 'dogrulanmadi'`,
  `tax_point_candidate_date = document_date`, `tax_point_candidate_source = 'belge_tarihi'`.
- UBL'de teslim/irsaliye/hizmet dönemi kanıtı varsa yalnızca **aday** tarih değişir; otomatik
  doğrulama yoktur.
- Müşavir/admin yalnızca mevcut kanıta dayalı adayı doğrular; kanıtsız tarih giremez, dönemi seçemez.
- Tutarlılık CHECK'i: `basis='dogrulanmadi'` ⇔ `tax_point_date`, `verified_at`, `verified_by` NULL.
- KDV raporu: kesin tutarlar yalnızca doğrulanmış `tax_point_date` ile; doğrulanmamışlar ayrı
  "bekleyenler" listesinde; liste boş değilse kesin beyan çıktısı üretilmez. İndirilecek KDV dönemi
  `posting_date` ayıdır.
- Örnek veri bulgusu: satış e-faturalarının hiçbirinde teslim/irsaliye/hizmet dönemi alanı yok.

## 3. Posting Date kuralları ve kayıt türü

`yevmiye_fisleri.kayit_turu`: `fatura`, `z_raporu`, `banka`, `manuel`, `tahakkuk`, `donem_sonu`,
`ters_kayit`, `belirsiz` (sınıflandırılamayan eski fişler).

| Tür | Varsayılan | Blok | Uyarı | Yetki |
|---|---|---|---|---|
| fatura / z_raporu | Belge ayı açıksa belge tarihi; kilitliyse otomatik değişmez | posting < document | VUK 219, KDVK 29/3 | Onay: müşavir/admin |
| banka | İşlem tarihi | posting < işlem tarihi | — | Aynı |
| manuel | Kullanıcı | — | belge yok | Aynı |
| tahakkuk / donem_sonu | Kullanıcı | — (belgeden önce olabilir) | — | Yalnızca müşavir/admin |
| ters_kayit | Bugün (açıksa) | posting < orijinal posting | KDV düzeltmesi bekliyor | Müşavir/admin |

Kilitli/kapalı döneme hiçbir rolle kayıt yapılamaz. VUK 219 ve KDVK 29/3 ilk aşamada yalnızca uyarı
(yapılandırılabilir eşik).

## 4. Dönemler

- Hesap dönemi yalnızca açık işlemle oluşturulur (takvim yılı öneri); aylar aynı işlemde açıkça üretilir.
- Ay kilidi: ayda taslak varsa varsayılan red; açık onayla taslaklar silinmeden ve tarihleri
  değiştirilmeden incelemede bırakılabilir.
- Kilit açma: yalnızca admin, gerekçeli, yalnızca son kilitli ay, `edefter_durumu = 'yok'` olmalı.
  **Yevmiye numaraları korunur.** Yeniden numaralandırma ayrı, gerekçeli, dry-run + parmak izi onaylı,
  denetim kayıtlı prosedürdür (Part 4).
- Eşzamanlılık: onay ayı `FOR SHARE`, kilitleme `FOR UPDATE` ile kilitler (SQLite: BEGIN IMMEDIATE).
  Kilit sırası: hesap dönemi → ay → sayaç.

## 5. Ters kayıt

- Muhasebe: açık dönemde seçilen tarih; orijinal belgeye bağlı; `reversal_of_id`; orijinal `reversed`.
- KDV: ters kayıt yeni vergi olayı değildir; KDV raporuna otomatik yansımaz, "KDV düzeltmesi bekleyenler"
  listesine düşer. Düzeltme beyannamesi/iade faturası kararı verilmeden vergi dönemi değişmez. KDV
  düzeltme kayıtları KDV modülündedir.

## 6. Aşamalar

3a şema (0003) → 3b PeriodService → 3c PostingDatePolicy + taslak/onay → 3d ters kayıt → 3e belge
düzeltme + tax point kanıt/doğrulama + `/tax/summaries` ön izleme etiketi → 3f backfill (dry-run).
Her aşama ayrı test, commit ve onayla.

## 7. Resmî doğrulama gereken noktalar

KDVK 10 senaryoları ve kısmi teslim; KDVK 29/3 süresi; VUK 219 süreleri; e-Defter kayıt/belge tarihi ve
şematron; e-Defter'i oluşmuş dönemde düzeltme/iptal; ters kayıt sonrası KDV düzeltmesi; tahakkuk ve
dönemsellik; BA/BS tarih esası; e-Fatura teslim/irsaliye alanlarının kanıt değeri.
