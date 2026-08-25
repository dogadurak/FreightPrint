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
| **Kaynak** | Eurostat, `road_go_ta_tott` — *Road freight transport by type of operation and type of transport (t, tkm, vehicle-km), annual* |
| **İndirme** | `https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/road_go_ta_tott` |
| **Birim** | Milyon araç-kilometre (`MIO_VKM`), `tra_type=TOTAL` |
| **Yıllar** | 2021–2024 |
| **Eurostat güncellemesi** | 2026-07-30 |
| **Lisans** | Eurostat yeniden kullanım politikası (kaynak gösterilerek serbest) |

`empty_share` = `EMPTY / TOTAL`, `intl_empty_share` = `EINTL / (LINTL + EINTL)`.

**Bilinen sınır — Türkiye bu veri setinde yok.** Eurostat yalnızca üye ve aday ülkelerin
bildirdiği karayolu anketlerini yayımlar; Türkiye bildirmiyor. Bu yüzden pilot
koridorun Türkiye ayağı için gözlem yok ve karşılaştırma AB tarafıyla sınırlıdır.
Bu, sonucun bir kusuru değil kapsamıdır ve öyle raporlanır.

**Ham JSON** (`eurostat_road_go_ta_tott.json`) türetilmiş CSV'nin yanında tutulur, böylece
türetmenin kendisi denetlenebilir.
