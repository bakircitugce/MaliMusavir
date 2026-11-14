# Part 3a Uygulama Planı: Migration 0003

Durum: **Onay bekliyor. Kod ve migration henüz yazılmadı.**

## Değişecek dosyalar

`app/core/models.py`, `alembic/versions/0003_belge_tarihleri_ve_kayit_turu.py` (yeni),
`tests/test_migrations.py`, `tests/test_part3a_schema.py` (yeni), `docs/CHANGELOG.md`.
Servis, router, panel ve kural kodu değişmez.

## Yeni alanlar

- `belgeler`: `document_date`, `document_number`, `document_date_extracted`,
  `document_number_extracted`, `tax_point_candidate_date`, `tax_point_candidate_source`,
  `tax_point_evidence` (JSON, `{}`), `tax_point_basis` (varsayılan `dogrulanmadi`),
  `tax_point_date`, `tax_point_verified_at`, `tax_point_verified_by` (FK, ON DELETE RESTRICT).
- `yevmiye_fisleri`: `posting_date` synonym (sütun değil), `document_date`, `document_number`,
  `document_type` (yalnızca belgesiz fişler), `kayit_turu` (varsayılan `belirsiz`).
- `muhasebe_donemleri`: `edefter_durumu` (varsayılan `yok`).

## Kısıtlar

- `ck_belge_tax_point_tutarlilik`: dört tax point alanı birlikte tutarlı.
- `ck_belge_tax_point_basis`, `ck_belge_tax_point_aday` (aday tarih ve kaynak birlikte).
- `ck_yevmiye_fisi_tek_kaynak`: kaynak belgeli fişte belge alanları boş.
- `ck_yevmiye_fisi_kayit_turu`, `ck_muhasebe_donemi_edefter`.
- Yeni UNIQUE yok (aşağıdaki açık hataya bakın).

## Mevcut verilere etki

Yeni sütunlar boş/varsayılan; hiçbir mevcut değer değişmez. `kayit_turu` sınıflandırması ve belge
tarihi doldurma 3f'te kanıtla, dry-run'lı ve onaylı yapılır.

## Uyumluluk, test, geri alma

SQLite batch modu; PostgreSQL standart CHECK (PostgreSQL'de çalıştırılmadı). Testler: gidiş-dönüş,
model farkı 0, kopya provası, tüm CHECK kombinasyonları, synonym, RESTRICT, mevcut paket iki modda.
Geri alma: `git revert` + `alembic downgrade 0002`; canlı veritabanında çalıştırılmayacak.

## Açık kritik hata: belge başına çift fiş

`source_document_id` doğrulamasında aynı belgeye bağlı birden fazla aktif fiş bulundu (çoğu özdeş taslak
çifti; birinde onaylı fiş + özdeş taslak → taslak onaylanırsa çift kayıt). Kök neden: arka plan kuyruğu
ile `/extract` eşzamanlı işleme; `source_document_id`'de tekillik yok; `existing_entry()` yalnızca en son
fişi görüyor. Ters kayıtlar orijinal belgeye bağlandığı için tekillik kuralı ters kayıtları dışarıda
tutmalı.

Önerilen düzeltme (onay bekliyor): (1) onayda "başka onaylı, ters kayıt olmayan fiş var mı" kontrolü ve
tek işleme yolu; (2) fazla taslaklar için dry-run raporu ve onaylı temizlik; (3) temizlik ve eski ters
kayıt bağlantısından sonra migration 0004: `UNIQUE(source_document_id) WHERE reversal_of_id IS NULL AND
status IN ('draft','pending_review','approved')`.
