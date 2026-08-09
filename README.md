# FreightPrint

Çok modlu yük taşımacılığı karbon ve rota analiz motoru.
Proje brifingi ve kapsam tanımı: [`PROJE_FreightPrint.md`](PROJE_FreightPrint.md).

**Durum:** Planın tüm fazları (0–8) tamamlandı. 230 test geçiyor.

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
| **Senaryo çubuğu** | Faktör esası (refakatsiz / refakatli / filo ort. / karşılaştırma esası) ve kapsam (TTW/WTW). Değiştirmek **anında** — yeniden rotalama yok |
| **KPI kartları** | Seçilen rotanın emisyonu · tam karayoluna işaretli fark · Monte Carlo belirsizlik aralığı · ro-ro esasına duyarlılık |
| **Harita + karşılaştırma** | Rota çizimi ve alternatiflerin moda göre yığılı emisyon çubukları |
| **Duyarlılık paneli** | Aynı rota, her faktör esası altında — noktalar tam karayolu çizgisini geçtiğinde karar değişir |

Rotalama pahalı (~6 sn, yedi OSRM çağrısı), fiyatlama bedava. Bu yüzden panonun sunduğu
her senaryo **tek istekte** hesaplanır; sonrasında geçiş yapmak sunucuya hiç gitmez.

**Yer arama neden tek cevap vermiyor?** Bir adı arka planda tek noktaya çözmek, sevkiyatı
yanlış vilayete göndermenin yoludur ve fark kendini belli etmez: doğrulama setinde bir
adın iki okuması rota mesafesinde **7 puan** fark veriyordu, ikisi de ekranda gayet normal
görünüyordu. Arama bu yüzden adayları listeler, seçim kullanıcınındır.

**Manşet KPI neden "tasarruf" değil?** Bu koridorda GLEC ro-ro faktörleriyle fark negatif
çıkıyor (aşağıdaki bölüm). Manşeti "tasarruf" diye kurmak ya negatif sayıyı yanlış
çerçevede gösterir ya da kullanıcıyı yaltaklanan faktörlere iter. Onun yerine manşet
**emisyon + işaretli fark**, dördüncü kart ise bu ürünün asıl bildiği şey: cevabın
muhasebe esasına ne kadar bağlı olduğu.

| Uç | İşlev |
|---|---|
| `GET /api/terminals` | Terminal listesi; servise bağlı olmayanlar işaretli |
| `GET /api/factor-sets` | Seçilebilir faktör setleri ve her birinin deniz esası |
| `POST /api/routes` | Sevkiyat → alternatifler, emisyon, tasarruf, belirsizlik |
| `POST /api/report` | Toplu dosya → rapor (`output_format`: `csv`/`xlsx`/`pdf`) |
| `GET /api/places` | Yer adı → aday konumlar (tek cevap değil, liste) |
| `GET /api/risk-zones` | İlan edilmiş savaş riski bölgeleri (GeoJSON) |
| `POST /api/report/jobs` | Toplu CSV → arka plan işi (202 + iş kimliği) |
| `GET /api/report/jobs/{id}` | İşin durumu ve ilerlemesi |
| `GET /api/report/jobs/{id}/file` | Biten işin raporu |
| `POST /api/compare` | İki sefer: doğrudan ve bir boğazdan kaçınan |

### Toplu rapor

Arayüzdeki "Toplu rapor" bölümünden CSV yükleyip rapor indirebilirsiniz. Zorunlu sütunlar
`origin_lon, origin_lat, destination_lon, destination_lat`; isteğe bağlı `reference,
origin_name, destination_name, tonnage`. Örnek dosya arayüzden indirilebilir.

Yükleme **arka plan işi** olarak çalışır: soğuk bir sevkiyat ~6 saniye ve yedi OSRM
çağrısı sürdüğü için 500 satırlık bir dosya hiçbir istek zaman aşımına sığmaz. Dosya
gönderilir, iş kimliği döner, arayüz ilerlemeyi sorar (`4/25 sevkiyat (%16)`), bitince
indirir. Satırlar birbirinden bağımsız olduğu için dörder dörder işlenir — ölçüldü,
100 satırda 36,6 sn yerine 9,4 sn. Sınır **OSRM istemcisinde**, çağıranda değil:
`OSRM_MAX_CONCURRENCY` (varsayılan 4) kaç isteğin aynı anda uçtuğunu kapatır. Bu sınır
çağıran başına olsaydı, dört işlik havuz içinde dörder satır demo sunucusuna aynı anda
**16 istek** bindirirdi — ölçüldü ve test edildi. Kendi OSRM'inizde artırılabilir.

> İşler **süreç belleğinde** tutulur. Bu, brifingin stateless tercihine uygun ve
> kullanıcının birkaç dakika izlediği bir şey için veritabanı gereksiz. Ama işler yeniden
> başlatmayı atlatmaz ve ikinci bir işçi süreci onları göremez — çok işçili dağıtım için
> önce paylaşılan bir depo gerekir.

**Çıktı biçimi.** Rapor üç biçimde indirilebilir ve üçü de aynı rakamları, aynı esas
beyanıyla taşır:

| Biçim | Ne için |
|---|---|
| **Excel** (`.xlsx`) | Lojistik biriminin düzenleyip dosyaladığı hâl. Veriler bir sayfada, **esas ve kaynaklar ayrı bir sayfada** — üstbilgi satırının üstüne yazılan bir not, veri sıralanıp kopyalandığında kaybolan ilk şeydir. |
| **PDF** | Müşterinin kendi raporlamasına eklediği hâl. Yatay A4, hesap esası bloğu, uyarılar ve yöntem notu. |
| **CSV** | Veri aktarımı. |

PDF'te **Bitstream Vera** fontu gömülür (reportlab ile birlikte gelir). Sebebi: reportlab'in
yerleşik fontları Latin-1'dir ve Latin-1'de **ı, ş, ğ, İ yoktur** — Türkçe bir rapor onlarla
bozuk çıkar. Gömülü font sayesinde konteynerde sistem fontu gerekmez.

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

Takım **ağa çıkmaz**: `conftest.py` her testin ağ erişimini kapatır ve dışarı çıkmaya
çalışan test başarısız olur (`@pytest.mark.network` ile muaf tutulabilir; şu an hiçbiri
değil). Bu bilinçli — canlı bir servise bağlı test, geliştiricinin makinesinde geçip
CI'da rastgele kırılır, ya da daha kötüsü orada da geçip taklidinin devre dışı kaldığını
gizler. Nitekim bu koruma eklenirken Nominatim'e gerçekten istek atan bir test bulundu.

## Konteyner ve CI

```bash
docker compose up -d app          # public OSRM ile, ön işleme gerekmez -> :8100
docker compose --profile self-hosted up -d   # yukarıdaki yerel OSRM ile
```

Önbellek `/app/var` altında, **`/app/data` altında değil**. Sebebi önemli: `data/`
referans veriyi taşır (faktörler, terminaller, risk bölgeleri) ve kodla birlikte
değişir. Önbelleği orada tutmak için oraya volume bağlarsanız referans veri de sabitlenir
ve düzeltilmiş bir emisyon faktörü yeniden dağıtılan konteynere hiç ulaşmaz.
`FREIGHTPRINT_CACHE_DIR` ile taşınabilir; verilmezse yerel geliştirme için `data/` olur.

`.github/workflows/ci.yml` üç iş koşar: hassas dosya kontrolü, testler, ve imajın
kurulup `/health` cevaplaması. Sır veya dış servis gerekmez.

Gizlilik kancasını kurun — elle hatırlamak kontrol sayılmaz:

```bash
python scripts/install_hooks.py
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
| `backend/app/core/risk.py` | Risk bölgesi kesişimi, geçilen boğazlar |
| `backend/app/core/cost.py` | ETS kapsamı ve sapma maliyeti |
| `backend/app/core/schedule.py` | Kapıdan kapıya süre: sürüş kuralları, aktarma, bekleme |
| `data/risk_zones.geojson` | İlan edilmiş savaş riski bölgeleri (elle sayısallaştırılmış) |
| `scripts/check_privacy.py` | Commit'e müşteri verisi sızmasını engelleyen kontrol |
| `frontend/` | Tek sayfa arayüz (MapLibre + vanilla JS, derleme adımı yok) |
| `backend/app/core/cache.py` | SQLite disk önbelleği — süreç yeniden başlasa da korunur |
| `backend/app/core/geocode.py` | Nominatim sarmalayıcı, ülke adı normalleştirme, disk önbelleği |
| `backend/app/core/validation.py` | Doğrulama veri setini okuma ve referansla karşılaştırma |
| `notebooks/validation_analysis.py` | Faz 3 analizi (defterin kaynağı) |

Rota arama, kalkış ve varış noktalarını grafa geçici düğüm olarak ekleyip k-en-kısa-yol
çalıştırır; böylece tam karayolu seçeneği doğal olarak karşılaştırma temeli hâline gelir.

## Deniz faktörünün seçimi — ro-ro, konteyner gemisi değildir

> Bu bölüm bir denetim sonucu değil. Proje brifingi (§3) sistemin "mevcut raporların
> hatasını bulan bir denetim aracı" olmadığını açıkça söylüyor; eldeki gerçek raporlar
> yalnızca **kendi hesabımızın tutarlılığını ölçmek** için kullanılıyor. Aşağıdaki
> karşılaştırma, motorun farklı faktör esasları altında nasıl davrandığını gösterir —
> herhangi bir firmanın raporu hakkında bir iddia değildir.

Ro-ro ile konteyner gemisi aynı emisyon esasına sahip değil. Ro-ro gemisi treyler taşır:
yükün yanında treylerin darasını da taşır, doluluk oranı düşüktür (%40) ve daha hızlı
seyreder. Konteyner gemisi büyüklüğündeki bir değeri (**~0,012 kg CO2/ton-km**) ro-ro
servisine uygulamak, bu yüzden esas hatasıdır — ve doğrulama veri setindeki hesaplar bu
esasla kurulmuş olduğu için bizim sayılarımızla kıyaslanabilir bir referans oluşturuyor.

GLEC Framework'ün ro-ro değerleri (Tablo 45, g CO2e/ton-km, TTW/WTW):

| Esas | TTW | WTW |
|---|---|---|
| Ortalama, sadece yük | 42 | 45 |
| **Sadece treyler** (bu sistemin varsayılanı) | **63** | **68** |
| Çekici + treyler | 93 | 100 |

Karayolu tarafında da esas farkı var: `reference` seti 0,121 taşırken GLEC'in 40 tonluk
çekicisi (konteyner esası, doluluk ve boş dönüş dâhil) **0,060**. İki fark da aynı yöne,
çok modlu taşımayı görece iyi gösterme yönüne çalışıyor.

Aynı sevkiyat, Pendik–Trieste–Köln (24 ton), üç esas altında:

| Faktör seti | Çok modlu | Tam karayolu | Fark |
|---|---|---|---|
| `reference` (karşılaştırma esası) | 1.262 kg | 7.304 kg | %83 |
| `glec` TTW | 4.324 kg | 3.622 kg | **−%19** |
| `glec` WTW | 4.760 kg | 4.527 kg | −%5 |

**Sonucun işareti, seçilen esasa bağlı.** Bu koridorda GLEC faktörleriyle çok modlu
taşıma karbon kazandırmıyor; demiryolu bacağı hâlâ net kazanç (tren 0,020'ye karşı
karayolu 0,060), fark ro-ro deniz bacağından geliyor.

Ürünün duruşu buradan çıkıyor: sistem **bir tasarruf vaat etmiyor, hangi esasla hesap
yaptığını beyan ediyor.** Her çıktıda faktör seti ve kapsam yazılı, faktör seti panoda
gizli bir varsayım değil öne çıkan bir kontrol, ve fark negatifse ağaç eşdeğeri sıfır
döner.

## Risk, maliyet ve süre

**Güzergâh riski.** Deniz bacaklarının izi searoute'tan alınıp ilan edilmiş savaş riski
bölgeleriyle kesiştirilir. Bölgeler `data/risk_zones.geojson` içinde, Joint War Committee
listesinden (JWLA-033) **elle sayısallaştırılmış basitleştirilmiş dikdörtgenler** —
kesin hukuki sınır değil, "rota bu alana giriyor mu" sorusunu cevaplamak için.

Poligonların doğruluğu iki testle sınanır: (1) bir bölge, kapsadığını iddia ettiği boğazın
gerçek koordinatını içermeli, (2) searoute bağımsız olarak "bu rota Süveyş'ten geçti"
diyorsa, iz Süveyş'i iddia eden bölgeyle kesişmeli. Bu testler yazıldığında iki yanlış
iddia yakalandı ve düzeltildi.

İzi olmayan bir deniz bacağı **"kontrol edilmedi"** olarak raporlanır, temiz sayılmaz.

**ETS maliyeti.** Şemanın gerçek coğrafi kuralı uygulanır: AEA içi seferler %100, bir ucu
dışarıda olanlar **%50**, ikisi de dışarıda olanlar kapsam dışı. Kademe: 2024 %40,
2025 %70, 2026 ve sonrası %100. Yalnızca deniz bacakları — karayolu ayrı bir şema (ETS2),
demiryolu denizcilik şemasında değil.

**Savaş risk primi hesaplanmaz.** Prim tekne değeri üzerinden pazarlıkla belirlenir ve
yayımlanmaz; kullanıcı girdisidir. Sistemin eklediği, sapmanın **hesaplanabilir** kısmı:
mesafe, süre, CO2 ve ETS farkı. Böylece armatörün faturası bir şeye karşı denetlenebilir.

**Kapıdan kapıya süre.** Mesafeden değil, üç parçadan oluşur: yolda geçen süre, terminal
aktarması ve kalkış beklemesi. Karayolunda AB sürüş kuralları uygulanır (günde 9 saat
sürüş, 4,5 saatte bir 45 dk mola, 11 saat günlük dinlenme) — bunlar olmadan Türkiye'den
Almanya'ya iki gün çıkardı. Deniz süreleri yayımlanmış tarifelerden (DFDS), yoksa
türetilir ve "tahmin" işaretlenir.

Gebze→Düsseldorf için sonuç: tam karayolu **3,1 gün**, çok modlu **5,5 gün** — ve farkın
üçte biri hiç hareket edilmeyen süre (18 sa aktarma + 25 sa bekleme).

## Terminal etki alanı

Haritanın altındaki **Terminal etki alanı** düğmesi, hangi terminalin nereye hizmet
ettiğini **sürüş süresiyle** gösterir. Alışıldık daire çizimi coğrafyanın olduğu her
yerde yanlıştır: Marmara, Alpler ve Boğaz, haritada yakın duran yerleri karayoluyla
saatlerce uzağa koyar. Ölçtük — noktaların **%14'ü** düz çizgi modelinin vereceğinden
farklı terminale düşüyor.

Sonuç bir **örnek ızgarasıdır, sınır değil.** İki örnek noktası arasındaki cevap
hesaplanmadı, bu yüzden harita ölçülen aralıkta kareler çizer; düzgün bir poligon,
kimsenin hesaplamadığı bir kesinlik iddia ederdi. Aralık cevabın içinde döner.

- OSRM'in `/table` servisi kullanılır: tek istekte çok noktaya matris. Izgara bu sayede
  kırk bin rota isteği yerine birkaç yüz tablo isteği eder.
- Hiçbir servisin uğramadığı terminaller **dışarıda bırakılır** (`connected_only`).
  Ambarlı'ya sürebilirsiniz ama gemiye binemezsiniz; ona etki alanı vermek, teslim edip
  mahsur kalacağınız bir bölge çizmek olurdu.
- OSRM'in rota bulamadığı nokta (açık deniz, ada) **atanmaz** — "en az kötü" terminale
  verilmez, yoksa denizi bir terminalin rengine boyardık.
- Süre sınırının ötesi de atanmaz; haritanın tamamını "en yakın" terminale boyamak
  olmayan bir etki alanı iddia etmektir.
- İlk hesap public OSRM ile ~26 sn, sonrası önbellekten anında.

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

### Frigo yük — saatle işler, km ile değil

Frigo yük seçildiğinde soğutmanın emisyonu **ayrı bir kalem** olarak eklenir; taşıma
rakamının içine karıştırılmaz. Sebep basit: taşıma sayısı yayınlanmış GLEC tablolarından
gelir, soğutma sayısı türetmedir, ikisini toplamak hangi yarının varsayıma dayandığını
gizlerdi.

GLEC reefer'ı yalnızca **konteyner gemisi** için ve **oran** olarak verir (Tablo 46: kuru
76'ya karşı reefer 145 g CO2e/TEU-km). Bu oran başka moda taşınamaz. Konteyner gemisinin
ton-km emisyonu düşük olduğu için soğutma ünitesi onu ikiye katlıyor gibi görünür; ro-ro'nun
emisyonu zaten bir mertebe yüksektir, aynı ünite orada küçük bir ek olur. Oranı ro-ro'ya
uygulamak ek yükü **yaklaşık dokuz kat** abartırdı.

Modlar arasında taşınabilen şey ünitenin **enerji çekişidir**, o da zamana bağlıdır. Bu
yüzden ek yük bir kez `g CO2e/ton/saat` olarak türetilir ve **kapıdan kapıya sürenin
tamamına** uygulanır — aktarmada ve kalkış beklemesinde geçen saatler dâhil, çünkü kutu o
saatlerde de fişte ve çekiyor.

Türetme zinciri `data/reefer_factors.csv` içinde açıkça yazılı: 145 − 76 = 69 g/TEU-km,
GLEC s.38'deki 10 ton/TEU ile 6,9 g/ton-km, 32 km/sa gemi hızıyla **221 g/ton/saat**. Üç
varsayım içerir ve hiçbiri bu hâliyle yayınlanmamıştır — bu yüzden satır `is_verified=no`
taşır ve arayüzde uyarı düşer.

Pratik sonucu: Gebze→Düsseldorf koridorunda multimodalin karayoluna göre cezası kuru yükte
%5 iken frigo yükte **%11'e** çıkıyor. Aradaki fark multimodalin 42,5 saatlik aktarma ve
bekleme süresi — km bazlı bir hesabın sıfır saydığı, soğutma faturasının üçte biri.

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
- **CLI geocoding kullanmaz.** Komut satırında koordinat girilmesi gerekiyor; arama
  yalnızca web arayüzünde var.
- **Referans mesafeler kendi aralarında çelişiyor.** `data/service_legs.csv` Pendik–Bari
  için 1755 km diyor, doğrulama veri seti aynı bacak için 1825 km. Hangisinin doğru
  olduğu henüz belirlenmedi — kendi deniz mesafemizi hesaplamadan hakem yok.
- **Ambarlı hiçbir servise bağlı değil.** Brifingde terminal olarak listeli ama servis
  bacağı yok, dolayısıyla rotalamaya hiç girmiyor. Bir test bunu görünür tutuyor.
- **Demiryolu transit süreleri türetilmiş.** Bu koridorlar için yayımlanmış tarife
  bulunamadı; 40 km/sa ortalamadan hesaplanıyor ve çıktıda "tahmin" olarak işaretli.
- **Aktarma süreleri sektör tipik değerleri**, ölçüm değil. Gümrük ve sınır kapısı
  bekleme süreleri hiç dâhil değil — gerçek kapıdan kapıya süre daha uzun olabilir.
- **Belirsizlik her moda aynı bandı uyguluyor.** Karayolu sapması %1,9 ölçüldü ama deniz
  sapması %12–43; ikisine de aynı %5 verilmesi en belirsiz bacakta sahte güven üretiyor.
- **`reference` seti WTW desteklemez.** Müşteri raporu yalnızca TTW değerleri verdiği için
  `--scope WTW` bu setle çalışmaz; `--factor-set glec` kullanın.
- **Demiryolu için dizel çekiş varsayılıyor.** GLEC'in dizel satırı hem TTW hem WTW verdiği
  için tutarlı bir çift oluşturuyor. Trieste–Köln gibi elektrikli koridorlarda gerçek değer
  0,0091 WTW, yani mevcut varsayım muhafazakâr (yüksek) yönde.
- **Yakıt tipi faktörleri kısmen doğrulanmamış.** HVO ve elektrik hâlâ `PLACEHOLDER`.
