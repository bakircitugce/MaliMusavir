# ADR 0001: Faz 0 temel altyapısı

## Durum

Kabul edildi.

## Karar

Uygulama FastAPI üzerinde, asenkron SQLAlchemy oturumlarıyla ve PostgreSQL hedefiyle
başlatılacaktır. Geliştirme ortamında PostgreSQL, Redis ve MinIO Docker Compose ile
sağlanır. Büro ve mükellef kimlikleri tenant modellerinde açıkça taşınır.

## Gerekçe

Belge işleme ve arka plan işleri sonraki fazlarda asenkron yürütüleceği için API ve
veri erişimi baştan bu modele uygun kurulmuştur. Vergi parametreleri tarih aralığıyla
saklanarak oranların kod içine sabitlenmesi önlenmiştir.

