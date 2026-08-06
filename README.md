# FreightPrint

Çok modlu yük taşımacılığı karbon ve rota analiz motoru.
Proje brifingi ve kapsam tanımı: [`PROJE_FreightPrint.md`](PROJE_FreightPrint.md).

**Durum:** Faz 1 — çekirdek rota motoru.

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
python -m app.cli --origin=29.4306,40.7889 --destination=13.7768,45.6495 \
                  --origin-name "Gebze" --destination-name "Trieste"
```

Deniz bacaklarının searoute ile hesaplanan mesafesini referansla karşılaştırmak için
`--compare-computed` ekleyin.

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
| `backend/app/core/network.py` | NetworkX grafı, terminal yükleme, en yakın terminal |
| `backend/app/core/road.py` | OSRM sarmalayıcı — serbest karayolu bacağı |
| `backend/app/core/sea.py` | searoute sarmalayıcı + Korint Kanalı gerçekçilik kontrolü |
| `backend/app/core/route.py` | İki nokta → çok modlu rota alternatifleri |

Rota arama, kalkış ve varış noktalarını grafa geçici düğüm olarak ekleyip k-en-kısa-yol
çalıştırır; böylece tam karayolu seçeneği doğal olarak karşılaştırma temeli hâline gelir.

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
