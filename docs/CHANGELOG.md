# Changelog

## 0.1.0 — Faz 0

- FastAPI uygulama iskeleti ve `/health` endpoint'i eklendi.
- PostgreSQL, Redis ve MinIO geliştirme servisleri için Compose tanımı eklendi.
- Büro, kullanıcı, mükellef ve yürürlük tarihli vergi parametresi modelleri eklendi.
- Türkçe sayı biçimi ve mizan denge doğrulaması için ilk içe aktarma çekirdeği eklendi.
- Sektör/meslek NACE Excel'i ile hiyerarşik mizan Excel'i yerel seed kaynağı olarak
  eklendi; her ikisi gerçek çalışma kitabı yapısına göre doğrulanıyor.
- Tekrarlanabilir ve dry-run destekli Excel import komutu eklendi; NACE kayıtları
  kaynak bazında, mizan hesapları mükellef bazında tekilleştiriliyor.
- Alembic yapılandırması ve temel test/lint ayarları eklendi.
## Faz 1 — Belge alımı

- Yerel depolama adapter'ı ve ileride S3/MinIO'ya genişletilebilecek storage arayüzü eklendi.
- Tenant header'larıyla tekli ve çoklu belge yükleme endpoint'leri eklendi.
- SHA-256 ile tenant bazlı mükerrer belge engelleme eklendi.
- XML, PDF, görsel ve Excel/CSV dosya türü tespiti eklendi.
- Ortak fatura çıkarım DTO'ları ve namespace bağımsız UBL-TR Invoice parser'ı eklendi.
- Metin tabanlı PDF sayfa metni çıkarımı `pypdf` ile eklendi; OCR sonraki alt
  adım olarak ayrıştırıldı.
- CSV ve XLSX için çok sayfalı, tekrar eden başlıkları güvenli biçimde normalize
  eden genel tablo okuyucu eklendi.
- Pillow ve pytesseract tabanlı Türkçe/İngilizce OCR çekirdeği eklendi; Tesseract
  eksikliği açık hata olarak raporlanıyor.
- Tenant filtreli belge listeleme ve detay endpoint'leri ile çıkarım durum alanları
  eklendi.
- Belge içerik imzası doğrulaması ve yerel, kalıcı olmayan extraction kuyruğu
  eklendi; üretimde Redis/Celery ile değiştirilmesi planlandı.
- Kural tabanlı belge sınıflandırması eklendi; düşük güvenli belgeler
  `needs_review` durumuna alınır.
- Mali müşavirin belge kategorisini ve inceleme durumunu düzeltip onaylayabildiği
  tenant filtreli review endpoint'i eklendi.
- Tenant kontrollü orijinal belge görüntüleme endpoint'i eklendi; storage yolu
  istemciye açılmıyor.
- Z raporu için kural tabanlı tarih, numara, ödeme dağılımı ve tutar çıkarımı
  eklendi; eksik veya dengesiz raporlar incelemeye işaretlenir.
- Gider pusulası ve serbest meslek makbuzu için yapılandırılmış alan çıkarıcılar
  ve anonim fixture yönergeleri eklendi.
- Parser sözleşmesini doğrulayan 50 sentetik Z raporu fixture testi eklendi;
  gerçek anonim belgeler için fixture manifest yapısı hazırlandı.
- Z raporu, gider pusulası, SMM, fatura ve banka ekstresi için UTF-8 anonim
  dosya fixture'ları ve manifest tabanlı testler eklendi.
- Sınıflandırıcıda Türkçe karakter normalizasyonu ve kısa anahtar kelime
  sınırları düzeltildi.
- Beş belge kategorisi için 50'şer sentetik fixture ve toplam 350 parametrik
  sınıflandırma/parser doğrulaması eklendi.
- Faz 2 için saf yevmiye fişi taslağı ve borç/alacak denge doğrulaması eklendi;
  henüz veritabanına veya otomatik muhasebe kuralına bağlanmadı.
- Alış faturası için hesap kodlarını dışarıdan alan, tutarları doğrulayan ve
  dengeli yevmiye taslağı üreten saf kural eklendi.
- Satış faturası için tahsilat, satış ve hesaplanan KDV satırlarını oluşturan
  dengeli yevmiye taslağı kuralı eklendi.
- Z raporu için nakit/POS borçları ile satış/KDV alacaklarını oluşturan ve
  ödeme dağılımını doğrulayan dengeli yevmiye kuralı eklendi.
- ÖKC/yazar kasa fişi için matrah, KDV ve ödeme hesabından dengeli yevmiye
  taslağı oluşturan saf kural eklendi.
- Binek araç yakıtında indirilebilir matrahı ve KKEG matrahını oranla ayıran,
  KDV ve ödeme satırlarını oluşturan saf kural eklendi.
- Özgün fişi değiştirmeden borç/alacak yönlerini çeviren ters kayıt taslağı
  üretimi eklendi.
- Muhasebe dönemleri ve kilitli döneme fiş yazılmasını engelleyen kontrol
  eklendi.
- Muhasebe dönemi açma/kilitleme servisleri; tarih çakışması ve tenant kapsamı
  kontrolleriyle eklendi.
- Taslak yevmiye fişini tenant kapsamı ve yeniden dengeleme kontrolüyle
  onaylayan servis eklendi; onaylanmış fiş tekrar onaylanamıyor.
- Onaylanmış fişi değiştirmeden ters kayıt taslağını veritabanına yazan servis
  eklendi.
- Tenant kapsamlı yevmiye fişi listeleme ve durum filtresi servisi eklendi.
- Tenant kapsamlı yevmiye fişi detayını ve sıralı satırlarını getiren servis
  eklendi.
- Onay kuyruğu için tenant kapsamlı toplu taslak fiş onay servisi eklendi.
- Tenant, mükellef, tarih ve durum filtreli dengeli yevmiye özeti raporu
  eklendi.
- Hesap bazında borç, alacak ve bakiye döndüren tenant kapsamlı mizan özeti
  eklendi.
- Hesap bazlı tarih sıralı muavin hareketleri ve kümülatif bakiye raporu
  eklendi.
- Banka hareketi modeli ve tek yönlü, tenant kapsamlı banka hareketi oluşturma
  servisi eklendi; otomatik eşleme ve POS mahsubu sonraki dilimdedir.
- Banka hareketlerini tenant, IBAN, tarih ve mutabakat durumuna göre listeleme
  servisi eklendi.
- Banka hareketini aynı tenant içindeki onaylı ve tutarı eşleşen yevmiye fişiyle
  mutabık yapan kontrollü servis eklendi.
- POS net yatışı, komisyon ve 108 POS hesabı arasında dengeli mahsup taslağı
  üreten kural eklendi.
- Banka mutabakat durumlarını ve eşleşen/eşleşmeyen tutarları özetleyen rapor
  servisi eklendi.
- Banka CSV içe aktarma parser'ı eklendi; başlık, tarih, Türkçe tutar ve tek
  yönlü borç/alacak doğrulamaları yapılıyor.
- Banka hareketleri için aynı tenant içinde dış ID üzerinden idempotent toplu
  içe aktarma servisi eklendi.
- Mutabık POS banka hareketinden onay gerektiren mahsup taslağı üreten servis
  eklendi.
- Banka içe aktarmada idempotency anahtarı IBAN ve dış hareket ID'si çifti
  olarak düzeltildi; farklı banka hesaplarının aynı ID'li hareketleri korunuyor.
- Banka hesabı unique kısıtı büro, mükellef ve IBAN kapsamına alındı; tenant'lar
  artık aynı IBAN'ı bağımsız kullanabiliyor.
- Banka hesabı listeleme ve aktif/pasif durum yönetimi tenant kapsamıyla eklendi.
- CSV metninden aktif banka hesabına idempotent toplu aktarımı tek servisle
  gerçekleştiren banka import akışı eklendi.
- Eşleşmeyen banka hareketleri için tenant filtreli, tarih/IBAN kapsamlı ve
  limit kontrollü mutabakat kuyruğu listeleme servisi eklendi.
- Eşleşmeyen banka hareketleri için tutar ve tarih yakınlığına göre onaylı
  yevmiye fişi adaylarını sıralayan, otomatik kayıt yapmayan aday servisi eklendi.
- Daha önce başka bir banka hareketiyle mutabık edilmiş yevmiye fişlerinin
  yeni aday listesinde tekrar önerilmesi engellendi.
- Tek banka yatışının birden fazla Z/yevmiye fişiyle eşleşebilmesi için,
  iki veya üç onaylı fişten oluşan toplam tutar adayları eklendi.
- Banka çıkış hareketleri için aday eşleştirmede yevmiye fişlerinin alacak
  toplamı kullanılacak şekilde yön kontrolü düzeltildi.
- Banka ekstreleri için XLSX parser ve aktif banka hesabına idempotent XLSX
  aktarım servisi eklendi.
- Harici paket eklemeden temel MT940 `:61:` hareket ve `:86:` açıklama parser'ı
  eklendi; borç/alacak yönleri banka hareketi sözleşmesine aktarılıyor.
- Banka karşı tarafı için VKN/TCKN öncelikli, normalize unvan destekli saf aday
  eşleme fonksiyonu eklendi; unvan eşleşmeleri inceleme gerektiriyor.
- Banka açıklamalarında anahtar kelime bazlı hesap önerisi eklendi; öneriler
  otomatik kayda dönüşmüyor ve inceleme gerektiriyor.
- Tenant header doğrulamalı banka API router'ı eklendi: hesap yönetimi,
  hareket listeleme, mutabakat kuyruğu ve tekli/toplu aday görüntüleme.
- CSV, XLSX ve MT940 banka içe aktarma servisleri HTTP endpoint'leri üzerinden
  tenant doğrulamasıyla erişilebilir hale getirildi.
- Aday yevmiye fişi seçimini mutabakata dönüştüren tenant kontrollü banka
  hareketi onay endpoint'i eklendi.
- Mutabakat kuyruğu için tenant ve tarih/IBAN filtreli özet endpoint'i eklendi;
  tutarlar JSON'da metin olarak döndürülüyor.
- Onaylı ve henüz mutabık edilmemiş 108 POS alacakları için 0-30, 31-60 ve
  61+ gün yaşlandırma raporu ve API endpoint'i eklendi.
- Eşleşmeyen banka hareketlerini tenant kapsamında göz ardı ederek kuyruktan
  çıkarma servisi ve API endpoint'i eklendi.
- Kredi kartı ekstresi/309 hesabı için net yatış, komisyon ve brüt 309 alacağını
  dengeleyen saf mahsup kuralı eklendi.
- Faz 4 başlangıcı olarak sağlayıcıdan bağımsız e-Fatura adapter protokolü,
  doğrulanan DTO'lar ve yalnızca test/demo amaçlı fake adapter eklendi.
- e-Fatura sağlayıcı registry'si eklendi; bilinmeyen sağlayıcılar açık hata ile
  reddediliyor ve otomatik fake adapter fallback'i yapılmıyor.
- e-Fatura gönderiminden önce mevcut UBL parser'ını kullanan doğrulama servisi
  eklendi; zorunlu belge kimliği, ödenecek tutar ve para birimi olmadan sağlayıcı
  adapter'ı çağrılmıyor.
- Gelen/giden e-Fatura sayfalarını güvenli biçimde tamamlayan pagination helper
  eklendi; sağlayıcının bozuk sayfa yanıtları ve sonsuz sayfalama açık hataya dönüşüyor.
- e-Fatura gönderimi için açık `approved=True` onayı zorunlu hale getirildi;
  doğrulanmış UBL belge bile kullanıcı onayı olmadan adapter'a gönderilmiyor.
- e-Fatura sayfalı senkronizasyonunda ETTN bazlı tekilleştirme eklendi;
  sağlayıcı aynı faturayı birden fazla sayfada döndürse bile sonuç tekrarlanmıyor.
- Faz 5 başlangıcı olarak idempotent ERP export adapter sözleşmesi ve yalnızca
  test/demo amaçlı bellek içi ERP adapter'ı eklendi.
- Onaylı yevmiye fişini tenant kapsamındaki satırlarıyla deterministik
  idempotency anahtarı kullanarak ERP adapter'ına aktaran servis eklendi.
- ERP adapter işlemleri için sınırlı retry helper eklendi; son hata sessizce
  yutulmuyor ve azami deneme sayısı doğrulanıyor.
- ERP aktarım sonuçlarını tenant, fiş ve idempotency anahtarıyla izleyen
  `erp_aktarimlari` modeli ve `0012_erp_aktarimlari` migration'ı eklendi.
- ERP export akışı artık denemeyi `pending`, başarıyı `exported`, adapter
  hatasını `failed` olarak kalıcı izliyor; başarısızlık sessizce kaybolmuyor.
- ERP aktarım kayıtlarını tenant ve durum filtresiyle listeleyen salt-okunur
  `/erp/exports` endpoint'i eklendi.
- Başarısız ERP aktarımını seçili sağlayıcı ve sınırlı deneme sayısıyla tekrar
  çalıştıran `/erp/exports/{export_id}/retry` endpoint'i eklendi; kayıtlı
  sağlayıcı yoksa işlem açıkça reddediliyor.
- Onaylı yevmiye fişlerinden, kullanıcı tarafından verilen hesap kodu
  önekleriyle kaynak fişleri koruyan dönemsel vergi özeti servisi ve
  `/tax/summaries` endpoint'i eklendi. Mevzuat oranı veya beyanname şeması
  varsayılmıyor.
- Tarihe göre tenant kapsamlı vergi parametresi çözümleme servisi ve
  `/tax/parameters/{key}` salt-okunur endpoint'i eklendi. Çakışan geçerlilik
  dönemleri sessizce seçilmiyor, açık hata dönüyor. Parametreler mevcut veri
  modeline uygun olarak büro kapsamındadır.
- Büro kapsamındaki vergi parametrelerini anahtar filtresiyle listeleyen
  `/tax/parameters` salt-okunur endpoint'i eklendi.
- İnsan onaylarından öğrenen tenant kapsamlı ilişki çekirdeği eklendi:
  özellik değerleri ham olarak saklanmadan parmak izi alınır, tekrar eden
  geri bildirimlerle güven skoru hesaplanır ve yalnızca en az 3 gözlem ve
  yüzde 80 güven sonrasında öneri üretilir. Otomatik muhasebe uygulaması yoktur.
  Endpoint'ler: `/learning/feedback` ve `/learning/suggestions`.
- Ek görseldeki tasarım yönüne uygun framework'süz onay kuyruğu dashboard'u
  eklendi: `/app`. Sol menü, güven rozetleri, önerilen kayıt, açıklama alanı,
  mobil görünüm ve onay/sonra etkileşimleri içerir.
- Dashboard, `buro_id` ve `mukellef_id` URL parametreleri verildiğinde gerçek
  `/documents` kuyruğunu yükler ve onay işlemini review API'sine gönderir;
  parametre yoksa güvenli demo görünümü korunur.
- Banka IBAN girişleri boşluklardan arındırılıp büyük harfe normalize ediliyor;
  banka hareketi idempotency kısıtı büro kapsamına alındı.
