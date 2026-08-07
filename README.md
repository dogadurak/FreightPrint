# FreightPrint

Çok modlu yük taşımacılığı karbon ve rota analiz motoru.
Proje brifingi ve kapsam tanımı: [`PROJE_FreightPrint.md`](PROJE_FreightPrint.md).

**Durum:** Faz 4 — web arayüzü çalışıyor.

## Kurulum

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Linux/macOS: .venv/bin/pip
```

### Karayolu rotalama (OSRM)

Varsayılan olarak public OSRM demo sunucusu kullanılır. **Bu yalnızca elle birkaç sorgu
içindir**: tek bir rota isteği yedi OSRM çağrısı yapar ve demo sunucusu hız sınırlıdır.
Kendi sunucunuz için `docker-compose.yml` hazır:

```bash
export OSRM_REGION=turkey OSRM_REGION_PATH=europe/turkey   # once kucukle dogrulayin
docker compose run --rm osrm-download
docker compose run --rm osrm-extract
docker compose run --rm osrm-partition
docker compose run --rm osrm-customize
docker compose up -d osrm

export OSRM_BASE_URL=http://localhost:5000
```

Tüm pilot koridor için `OSRM_REGION=europe OSRM_REGION_PATH=europe` kullanın — yaklaşık
28 GB indirme ve ~64 GB RAM gerektirir. Türkiye bölgesi doğrulama için yeterlidir.

> Bu yapılandırma bu makinede **çalıştırılarak doğrulanmadı** (Docker kurulu değil).
> Uygulamanın `OSRM_BASE_URL` değişkenini doğru kullandığı test edildi.

Rota yanıtları `data/route_cache.sqlite` içinde saklanır, süreç yeniden başlasa bile
korunur (soğuk istek ~6 sn, önbellekten ~0,01 sn).

## Kullanım — web arayüzü

```bash
cd backend
python -m uvicorn app.main:app --reload
```

Tarayıcıda `http://127.0.0.1:8000`. API dokümantasyonu `/docs` altında.

Pano dört bölümden oluşur:

| Bölüm | Ne gösterir |
|---|---|
| **Senaryo çubuğu** | Faktör esası (refakatsiz / refakatli / filo ort. / müşteri raporu) ve kapsam (TTW/WTW). Değiştirmek **anında** — yeniden rotalama yok |
| **KPI kartları** | Seçilen rotanın emisyonu · tam karayoluna işaretli fark · Monte Carlo belirsizlik aralığı · ro-ro esasına duyarlılık |
| **Harita + karşılaştırma** | Rota çizimi ve alternatiflerin moda göre yığılı emisyon çubukları |
| **Duyarlılık paneli** | Aynı rota, her faktör esası altında — noktalar tam karayolu çizgisini geçtiğinde karar değişir |

Rotalama pahalı (~6 sn, yedi OSRM çağrısı), fiyatlama bedava. Bu yüzden panonun sunduğu
her senaryo **tek istekte** hesaplanır; sonrasında geçiş yapmak sunucuya hiç gitmez.

**Manşet KPI neden "tasarruf" değil?** Bu koridorda doğru GLEC ro-ro faktörleriyle tasarruf
negatif çıkıyor (aşağıdaki bulgu). Manşeti "tasarruf" diye kurmak ya negatif sayıyı yanlış
çerçevede gösterir ya da kullanıcıyı yaltaklanan faktörlere iter. Onun yerine manşet
**emisyon + işaretli fark**, dördüncü kart ise bu ürünün asıl bildiği şey: cevabın
muhasebe esasına ne kadar bağlı olduğu.

| Uç | İşlev |
|---|---|
| `GET /api/terminals` | Terminal listesi; servise bağlı olmayanlar işaretli |
| `GET /api/factor-sets` | Seçilebilir faktör setleri ve her birinin deniz esası |
| `POST /api/routes` | Sevkiyat → alternatifler, emisyon, tasarruf, belirsizlik |
| `POST /api/report` | Toplu CSV → indirilebilir rapor |

### Toplu rapor

Arayüzdeki "Toplu rapor" bölümünden CSV yükleyip rapor indirebilirsiniz. Zorunlu sütunlar
`origin_lon, origin_lat, destination_lon, destination_lat`; isteğe bağlı `reference,
origin_name, destination_name, tonnage`. Örnek dosya arayüzden indirilebilir.

Her sevkiyat için **en düşük emisyonlu** seçenek raporlanır — bu tam karayolu da olabilir.
Rotalanamayan bir sevkiyat kendi satırında hatasıyla görünür, diğerlerini düşürmez.
Rapor dosyasının başında hangi faktör seti ve kapsamla üretildiği yazar; kaynağı
belirtilmeyen bir karbon rakamı alıcı tarafından denetlenemez.

Haritada karayolu bacakları **gerçek OSRM güzergâhı**, deniz ve demiryolu bacakları
**kesikli düz çizgi** olarak çizilir — bunlar şematiktir, ölçülmüş güzergâh değildir.

## Kullanım — komut satırı

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
| `backend/app/main.py` | FastAPI girişi, arayüzü statik olarak sunar |
| `backend/app/api/` | Pydantic şemaları ve REST uçları |
| `backend/app/core/report.py` | Toplu sevkiyat dosyası → rapor |
| `scripts/check_privacy.py` | Commit'e müşteri verisi sızmasını engelleyen kontrol |
| `frontend/` | Tek sayfa arayüz (MapLibre + vanilla JS, derleme adımı yok) |
| `backend/app/core/cache.py` | SQLite disk önbelleği — süreç yeniden başlasa da korunur |
| `backend/app/core/geocode.py` | Nominatim sarmalayıcı, ülke adı normalleştirme, disk önbelleği |
| `backend/app/core/validation.py` | Doğrulama veri setini okuma ve referansla karşılaştırma |
| `notebooks/validation_analysis.py` | Faz 3 analizi (defterin kaynağı) |

Rota arama, kalkış ve varış noktalarını grafa geçici düğüm olarak ekleyip k-en-kısa-yol
çalıştırır; böylece tam karayolu seçeneği doğal olarak karşılaştırma temeli hâline gelir.

## ⚠️ Deniz faktörü bulgusu — ro-ro, konteyner gemisi değildir

Doğrulama veri setindeki firma deniz bacağı için **0,012 kg CO2/ton-km** kullanmış. Bu bir
konteyner gemisi büyüklüğünde bir değer — ama raporladığı servisler (Pendik, Yalova, Bari,
Patras, Sète) **ro-ro**. Ro-ro gemisi treyler taşır: yükün yanında treylerin darasını da
taşır, doluluk oranı düşüktür (%40) ve daha hızlı seyreder.

GLEC Framework'ün ro-ro değerleri (Tablo 45, g CO2e/ton-km, TTW/WTW):

| Esas | TTW | WTW |
|---|---|---|
| Ortalama, sadece yük | 42 | 45 |
| **Sadece treyler** (bu sistemin varsayılanı) | **63** | **68** |
| Çekici + treyler | 93 | 100 |

Aynı anda karayolu faktörü de ters yönde sapıyor: rapor 0,121 kullanmış, GLEC'in 40 tonluk
çekicisi (konteyner esası, doluluk ve boş dönüş dâhil) **0,060**. İki sapma da aynı yöne,
çok modlu taşımayı olduğundan iyi gösterme yönüne çalışıyor.

Aynı sevkiyat, Pendik–Trieste–Köln (24 ton):

| Faktör seti | Çok modlu | Tam karayolu | Tasarruf |
|---|---|---|---|
| `reference` (müşteri raporu) | 1.262 kg | 7.304 kg | %83 |
| `glec` TTW | 4.324 kg | 3.622 kg | **−%19** |
| `glec` WTW | 4.760 kg | 4.527 kg | −%5 |

**Sonuç: bu koridorda "çok modlu taşıma karbon kazandırır" iddiası GLEC faktörleriyle
savunulamıyor.** Demiryolu bacağı hâlâ net kazanç (tren 0,020'ye karşı karayolu 0,060),
sorun ro-ro deniz bacağında. Sistem bu yüzden hangi faktör setiyle hesap yaptığını her
çıktıda yazar ve tasarruf negatifse ağaç eşdeğerini sıfır döndürür.

## Emisyon hesabı

`bacak_emisyonu = mesafe_km × ton × faktör`, faktör doluluk oranı ve boş dönüş payına göre
düzeltilir. Tam karayolu senaryosu karşılaştırma temelidir; tasarruf bu ikisinin farkıdır.

Faktörler koda gömülü değil — `data/emission_factors.csv` her satırda kaynağını, yılını,
kapsamını (TTW/WTW) ve **doğrulanmış olup olmadığını** taşır. Doğrulanmamış bir faktör
kullanıldığında çıktıya uyarı düşer.

Doğrulanmış faktör setleri:

| Set | Kapsam | Deniz esası | Kaynak |
|---|---|---|---|
| `reference` | TTW | 0,012 | Müşteri raporunun kendi değerleri — karşılaştırma için |
| `glec` | TTW + WTW | 0,063 refakatsiz | GLEC Framework 2019 (Tem 2022), Tablo 38/42/45 |
| `glec_accompanied` | TTW + WTW | 0,093 refakatli | Çekici ve şoför de gemide |
| `glec_freight_average` | TTW + WTW | 0,042 filo ort. | Clean Shipping Index ölçümü |

```bash
python -m app.cli --origin=... --destination=... --factor-set glec --scope WTW
```

**Refakatli/refakatsiz seçimi sonucun işaretini değiştirir** — bu yüzden gizli bir varsayım
değil, ayrı bir faktör seti olarak açıkta duruyor.

Bir faktör istenen sette yoksa sistem **hata verir**, başka sete düşmez. Çıktının "GLEC ile
hesaplandı" deyip bir bacağı başka yerden alması, standart iddiası taşıyan bir üründe
kabul edilemez.

### Doluluk oranı ve çift sayım

Yayınlanmış faktörler kendi doluluk varsayımlarını zaten içerir — GLEC karayolu satırı %72
doluluk ve %30 boş dönüş varsayıyor. Bu değerler `basis_load_factor` ve `basis_empty_share`
sütunlarında tutulur; `--load-factor` verdiğinizde önce yayıncının varsayımı **çıkarılır**,
sonra sizinki uygulanır. İkisi birden uygulanırsa faktör 1,8 katına çıkardı.

Hiçbir şey vermezseniz faktör yayınlandığı hâliyle kullanılır — kaynağının önerdiği budur.

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
  olduğu henüz belirlenmedi — kendi deniz mesafemizi hesaplamadan hakem yok.
- **Ambarlı hiçbir servise bağlı değil.** Brifingde terminal olarak listeli ama servis
  bacağı yok, dolayısıyla rotalamaya hiç girmiyor. Bir test bunu görünür tutuyor.
- **Süre ve servis sıklığı veri modelinde yok.** `service_legs.csv` yalnızca mesafe
  taşıyor; deniz ve demiryolu bacaklarının transit süresi hesaplanmıyor, bu yüzden
  "en hızlı rota" sorusu henüz cevaplanamıyor.
- **Belirsizlik her moda aynı bandı uyguluyor.** Karayolu sapması %1,9 ölçüldü ama deniz
  sapması %12–43; ikisine de aynı %5 verilmesi en belirsiz bacakta sahte güven üretiyor.
- **`reference` seti WTW desteklemez.** Müşteri raporu yalnızca TTW değerleri verdiği için
  `--scope WTW` bu setle çalışmaz; `--factor-set glec` kullanın.
- **Demiryolu için dizel çekiş varsayılıyor.** GLEC'in dizel satırı hem TTW hem WTW verdiği
  için tutarlı bir çift oluşturuyor. Trieste–Köln gibi elektrikli koridorlarda gerçek değer
  0,0091 WTW, yani mevcut varsayım muhafazakâr (yüksek) yönde.
- **Yakıt tipi faktörleri kısmen doğrulanmamış.** HVO ve elektrik hâlâ `PLACEHOLDER`.
