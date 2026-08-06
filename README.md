# FreightPrint

Çok modlu yük taşımacılığı karbon ve rota analiz motoru.
Proje brifingi ve kapsam tanımı: [`PROJE_FreightPrint.md`](PROJE_FreightPrint.md).

**Durum:** Faz 2 — emisyon motoru.

## Kurulum

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Linux/macOS: .venv/bin/pip
```

## Kullanım

Kalkış ve varış noktası `lon,lat` olarak verilir. Negatif boylamda `--origin=...`
biçimini kullanın (aksi hâlde argparse bunu parametre sanır).

```bash
cd backend
python -m app.cli --origin=29.4306,40.7889 --destination=6.7735,51.2277 \
                  --origin-name "Gebze" --destination-name "Dusseldorf" --tonnage 24
```

Sık kullanılan seçenekler:

| Seçenek | Ne yapar |
|---|---|
| `--tonnage` | Taşınan yük (ton), varsayılan 24 |
| `--scope` | `TTW` veya `WTW` — faktör kapsamı |
| `--fuel` | Karayolu yakıtı (`diesel`, `hvo`, `lng`, `electric`) |
| `--load-factor` | Doluluk oranı 0-1 |
| `--empty-return` | Boş dönüş payı 0-1 |
| `--load-uncertainty` | Doluluğun ne kadar altına düşebileceği |
| `--compare-computed` | Deniz bacağını searoute ile de hesaplayıp referansla karşılaştır |

## Testler

```bash
cd backend
python -m pytest tests/ -q
```

## Mimari (Faz 1 kapsamı)

| Dosya | Sorumluluk |
|---|---|
| `data/terminals.geojson` | Terminal noktaları (brifing Bölüm 4.3) |
| `data/service_legs.csv` | Deniz/demiryolu servis kenarları (Bölüm 4.4) |
| `data/emission_factors.csv` | Versiyonlu faktör tablosu — kaynak, yıl, kapsam, doğrulanmış mı |
| `data/tree_factors.csv` | Ağaç eşdeğeri katsayıları |
| `backend/app/core/network.py` | NetworkX grafı, terminal yükleme, en yakın terminal |
| `backend/app/core/road.py` | OSRM sarmalayıcı — serbest karayolu bacağı, feribot ayrımı |
| `backend/app/core/sea.py` | searoute sarmalayıcı + Korint Kanalı gerçekçilik kontrolü |
| `backend/app/core/route.py` | İki nokta → çok modlu rota alternatifleri |
| `backend/app/core/emissions.py` | Faktör uygulama, tam karayolu karşılaştırması, ağaç eşdeğeri |
| `backend/app/core/uncertainty.py` | Monte Carlo belirsizlik aralığı |

Rota arama, kalkış ve varış noktalarını grafa geçici düğüm olarak ekleyip k-en-kısa-yol
çalıştırır; böylece tam karayolu seçeneği doğal olarak karşılaştırma temeli hâline gelir.

## Emisyon hesabı

`bacak_emisyonu = mesafe_km × ton × faktör`, faktör doluluk oranı ve boş dönüş payına göre
düzeltilir. Tam karayolu senaryosu karşılaştırma temelidir; tasarruf bu ikisinin farkıdır.

Faktörler koda gömülü değil — `data/emission_factors.csv` her satırda kaynağını, yılını,
kapsamını (TTW/WTW) ve **doğrulanmış olup olmadığını** taşır. Doğrulanmamış bir faktör
kullanıldığında çıktıya uyarı düşer. HVO/LNG/elektrik satırları şu an `PLACEHOLDER`
kaynaklıdır ve rapora girmeden önce GLEC/ISO 14083 değeriyle değiştirilmelidir.

## Bilinen sınırlar

- **searoute Korint Kanalı'ndan geçiyor.** Kütüphane, gerçek ro-ro/konteyner gemilerinin
  geçemeyeceği Korint Kanalı'nı kullanan kısayollar üretiyor ve bu `restrictions`
  parametresiyle engellenemiyor. Bu yüzden servis bacaklarında referans mesafe esas
  alınır; searoute değeri yalnızca karşılaştırma için hesaplanır ve kanaldan geçen
  rotalar "kullanılamaz" olarak işaretlenir.
- **Demiryolu mesafeleri yalnızca referans tablodan gelir.** TEN-T/OpenRailwayMap
  entegrasyonu henüz yok, bu yüzden demiryolu bacağı hesaplanmıyor.
- **Karayolu için public OSRM demo sunucusu kullanılıyor.** Hız sınırlı; prodüksiyon
  için `OSRM_BASE_URL` ortam değişkeniyle kendi OSRM örneğinizi gösterin.
- **Geocoding yok.** Şehir adı değil, koordinat girilmesi gerekiyor.
- **WTW faktörleri henüz yok.** Tablo kapsamı destekliyor ama yalnızca TTW satırları
  doğrulanmış durumda; `--scope WTW` şu an faktör bulamaz.
- **Yakıt tipi faktörleri doğrulanmamış.** Dizel dışındaki her seçenek `PLACEHOLDER`.
