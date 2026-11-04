# Anonim belge fixture'ları

Bu klasör yalnızca anonimleştirilmiş, sentetik veya açıkça paylaşılabilir belge
örnekleri içindir. Gerçek müşteri belgesi, VKN/TCKN, IBAN veya kişisel veri
eklenmez.

Her fixture, ilgili parser için bir doğruluk testiyle birlikte eklenmelidir.
OCR fixture'larında beklenen metin ve kabul edilebilir güven eşiği ayrıca
belirtilir.

Fixture listesi `manifest.py` içinde tutulur. Dosya adları kategoriyle
başlamalı ve içerikler UTF-8 olmalıdır. Yeni bir fixture eklerken manifest ve
`tests/` altındaki ilgili parametrik test birlikte güncellenir.
