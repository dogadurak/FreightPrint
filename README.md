# FreightPrint

Çok modlu yük taşımacılığı karbon ve rota analiz motoru.
Proje brifingi ve kapsam tanımı: [`PROJE_FreightPrint.md`](PROJE_FreightPrint.md).

**Durum:** Faz 3 — doğrulama tamamlandı.

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
| `backend/app/core/geocode.py` | Nominatim sarmalayıcı, ülke adı normalleştirme, disk önbelleği |
| `backend/app/core/validation.py` | Doğrulama veri setini okuma ve referansla karşılaştırma |
| `notebooks/validation_analysis.py` | Faz 3 analizi (defterin kaynağı) |

Rota arama, kalkış ve varış noktalarını grafa geçici düğüm olarak ekleyip k-en-kısa-yol
çalıştırır; böylece tam karayolu seçeneği doğal olarak karşılaştırma temeli hâline gelir.

## Emisyon hesabı

`bacak_emisyonu = mesafe_km × ton × faktör`, faktör doluluk oranı ve boş dönüş payına göre
düzeltilir. Tam karayolu senaryosu karşılaştırma temelidir; tasarruf bu ikisinin farkıdır.

Faktörler koda gömülü değil — `data/emission_factors.csv` her satırda kaynağını, yılını,
kapsamını (TTW/WTW) ve **doğrulanmış olup olmadığını** taşır. Doğrulanmamış bir faktör
kullanıldığında çıktıya uyarı düşer. HVO/LNG/elektrik satırları şu an `PLACEHOLDER`
kaynaklıdır ve rapora girmeden önce GLEC/ISO 14083 değeriyle değiştirilmelidir.

## Doğrulama

Sistem, gerçek bir lojistik firmasının iki müşteri için hazırladığı karbon raporlarındaki
34 sevkiyatla karşılaştırıldı. Veri seti gerçek müşteri bilgisi içerdiği için depoda
**yoktur**; doğrulama testleri veri yoksa kendini atlar.

| Ölçüt | Hedef | Sonuç | Kapsam |
|---|---|---|---|
| Emisyon tutarlılığı — tam karayolu | fark < %1 | **34/34 satır**, hata ≈ 0 | 34/34 |
| Emisyon tutarlılığı — çok modlu | fark < %1 | **19/22 satır**, eşleşenlerde hata = 0 | 22/22 |
| Karayolu mesafe sapması | raporlanabilir | MAPE **%1,9**, 30/30 satır %10 içinde | 30/34 |
| Deniz mesafe sapması | raporlanabilir | temiz %4,2 — Korint geçen %21,9 | n=1 / n=5 |

Eşleşmeyen üç satır, kendi bildirdiği km sütunlarının ürettiğinden **daha düşük** bir CO2
raporluyor. Farkın karayolu bacağından geldiği yorumu tutarlıdır ama yalnız bu veriyle
kanıtlanamaz; kesin olan, kaynak raporun kendi içinde tutarsız olduğudur.

**Bu sonuçların kabul edilmiş sınırları:**

- Karayolu metriği 34 satırın 30'unu kapsıyor. Dört varış noktası coğrafi kodlanamadı,
  çünkü posta kodları kaynak veride eksik ya da hatalı yazılmış (biri gereken hane
  sayısından kısa, biri harf eki eksik, biri CEDEX kodu). Yanlış bir konuma zorlamak
  yerine çözümsüz bırakıldı.
- Bir varış noktasının adı ülkesinde birden fazla yerleşime karşılık geliyor. Seçilen
  aday −%3,3 sapma verir; diğer aday −%10,3 verir ve %10 ifadesini kırar. Hangisinin
  kastedildiği kaynak veriden anlaşılmıyor.
- Korint karşılaştırmasının kontrol grubu **tek bacak**. O bacak aynı zamanda ağdaki tek
  Adriyatik-içi bağlantı olduğundan "kanaldan geçiyor" ile "Doğu Akdeniz çıkışlı"
  değişkenleri ayrılamıyor. Tablo işaret eder, kanıtlamaz — kanıt Faz 0'daki doğrudan
  gözlemdir (rota koordinatları kanalın üzerinden geçiyor).

Analizi yeniden üretmek için:

```bash
python notebooks/build_notebook.py   # betikten defteri üret
jupyter notebook notebooks/validation_analysis.ipynb
```

Defter **çıktısız** işlenir: çalıştırıldığında grafikleri ve sayıları yerelde üretir,
ama sonuçlar depoya yazılmaz. Defterin metninde açıklama amaçlı birkaç örnek servis
rotası ve mesafe geçer; sevkiyat satırları, müşteri adları ve tonajlar geçmez.

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
- **CLI geocoding kullanmaz.** Şehir adı değil, koordinat girilmesi gerekiyor.
  `geocode.py` yalnızca doğrulama analizinde kullanılıyor; birden fazla yerleşimin
  aynı adı taşıdığı durumlarda sessizce yanlış eşleşme üretebildiği için hesap
  yoluna dâhil edilmedi.
- **Referans mesafeler kendi aralarında çelişiyor.** `data/service_legs.csv` Pendik–Bari
  için 1755 km diyor, doğrulama veri seti aynı bacak için 1825 km. Hangisinin doğru
  olduğu henüz belirlenmedi.
- **WTW faktörleri henüz yok.** Tablo kapsamı destekliyor ama yalnızca TTW satırları
  doğrulanmış durumda; `--scope WTW` şu an faktör bulamaz.
- **Yakıt tipi faktörleri doğrulanmamış.** Dizel dışındaki her seçenek `PLACEHOLDER`.
