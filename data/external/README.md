# Dışarıdan doğrulanmış veri

Bu klasördeki dosyalar **indirilmiştir, üretilmemiştir.** Motorun kendi hesabı bunlara
karşı sınanır; hiçbiri hesabın girdisi değildir.

Ayrımın sebebi şu: bir modeli kendi varsayımıyla doğrulamak doğrulama değildir. Bu
projede karbon motorunun ağırlığı, gerçek bir müşterinin rakamını yeniden üretmesinden
geliyordu — ama o kanıt yalnızca tek bir modüle aitti. Buradaki dosyalar aynı disiplini
diğer modüllere taşımak için var.

## empty_running_eurostat.csv

| | |
|---|---|
| **Kaynak** | Eurostat, `road_go_ta_vm` — *Road freight transport vehicle movements by loading status, type of transport and territorial coverage (vehicle-km, journeys), annual* |
| **İndirme** | `https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/road_go_ta_vm` |
| **Birim** | Milyon araç-kilometre (`MIO_VKM`), `tra_type=TOTAL` |
| **Yıllar** | 2022–2024 |
| **Eurostat güncellemesi** | 2026-07-30 |
| **Lisans** | Eurostat yeniden kullanım politikası (kaynak gösterilerek serbest) |

`empty_share` = `EMPTY / TOTAL` (tüm taşıma), `intl_empty_share` = aynısı `tra_cov=INTL` için.
Çapraz tablo doğrudan yayımlandığı için türetme tek bölmeden ibarettir.

**Bilinen sınır — Türkiye ve Sırbistan bu veri setinde yok.** Eurostat yalnızca bu ankete bildirim yapan ülkeleri
yayımlar; Türkiye ve Sırbistan yapmıyor. Pilot koridorun 2.515 karayolu kilometresinin
**754'ü (%30) bu ikisinde geçiyor.**

Çözüm, eksik ülke için bir sayı uydurmak ya da komşusunu ikame etmek değil:
`corridor_empty_running()` gözlemi rotanın kendi kilometreleriyle ağırlıklandırır ve
**hangi payı kapsadığını birlikte döndürür.** Pilot koridor için sonuç %17,4 oranı ve
%70 kapsamdır. Kapsamı söylenmeyen bir ağırlıklı ortalama, kontrol edilene kadar
doğru görünen türden bir sayıdır.

**Ham JSON** (`eurostat_road_go_ta_vm.json`) türetilmiş CSV'nin yanında tutulur, böylece
türetmenin kendisi denetlenebilir.
