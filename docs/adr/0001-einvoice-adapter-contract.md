# ADR 0001: e-Fatura adapter sözleşmesi

## Karar

e-Fatura entegratörleri sağlayıcıdan bağımsız `EInvoiceAdapter` protokolü arkasında
çalışacaktır. Gerçek sağlayıcı API davranışı, resmi dokümantasyon ve test ortamı
temin edilmeden kodlanmayacaktır.

## Sonuç

Çekme, fatura düzenleme, durum sorgulama ve iptal/itiraz akışları aynı sözleşmeyle
test edilebilir. `FakeEInvoiceAdapter` yalnızca test ve yerel demo içindir; gerçek
fatura gönderimi yapmaz.
