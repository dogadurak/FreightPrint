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
| **Türetme** | `python scripts/import_eurostat.py` (`--fetch` taze indirir, `--check` doğrular) |
| **Lisans** | Eurostat yeniden kullanım politikası (kaynak gösterilerek serbest) |

`empty_share` = `EMPTY / TOTAL` (tüm taşıma), `intl_empty_share` = aynısı `tra_cov=INTL` için.
Çapraz tablo doğrudan yayımlandığı için türetme tek bölmeden ibarettir.

**Türetme betiği ağa çıkmaz; indirme ayrı bir adımdır.** Sebebi şu: yeniden türetme
çevrimdışı çalışmazsa, CSV'nin yanında duran ham yanıt hiçbir şey kanıtlamaz. `--check`
işlenmiş dosyanın hâlâ ham yanıttan birebir üretilip üretilmediğine bakar ve test
takımında koşar — kaynağından ayrı düşmüş türetilmiş bir dosya, hiç olmamasından kötüdür,
çünkü hâlâ kanıt gibi görünür.

**Bildirilen sıfır ile bildirilmemiş ayrı yazılır.** Kıbrıs 2022'de uluslararası
taşımada 2 milyon araç-km bildiriyor: 1 yüklü, **0 boş**. O sıfır bir gözlemdir. Boş
bırakmak onu Türkiye'yle aynı işarete sokardı — Türkiye gerçekten bildirmiyor — ve ilk
türetme tam olarak bunu yapıyordu. Betik bunu düzeltiyor, bir test de sabitliyor.

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

## roro_intensity_mrv.csv

| | |
|---|---|
| **Kaynak** | EU MRV — *Publication of information*, THETIS-MRV (EMSA) |
| **İndirme** | https://mrv.emsa.europa.eu/#public/emission-report — elle, tek tıkla |
| **Türetme** | `python scripts/import_mrv.py "data/external/<dosya>.xlsx"` |
| **Dönemler** | 2023, 2024, 2025 · 684 gemi-yılı |
| **Kapsam** | 5.000 GT üzeri, AB/AEA limanlarına uğrayan gemiler; **doğrulayıcı onaylı** |

Yalnızca `Ro-ro ship`, `Ro-pax ship` ve `Container/ro-ro cargo ship` tutulur. Araç
gemisi otomobil, yolcu gemisi insan taşır; GLEC'in treyler faktörünü onlara karşı
tutmak farklı gemileri kıyaslayıp buna doğrulama demek olurdu.

**Ama gözlemde tek bir ro-pax yok — ve bu, filtrenin değil yayının sonucu.** 2025
dönemindeki 415 ro-pax gemisinin *tamamı* ve 67 konteyner/ro-ro gemisinin *tamamı*
kütle esaslı taşıma işi bildirmiyor; MRV ro-pax'ın taşıma işini yolcu üzerinden ölçtüğü
için ton-mil sütunu bütün sınıf için `Division by zero!` dönüyor. Elde kalan filo saf
ro-ro yük gemileri. Bunun pratik sonucu: GLEC'in **refakatli** satırı (çekici ve sürücü
yükle birlikte, 0,093) ağırlıkla ro-pax'ta seyreden bir trafiği tarif ediyor, yani bu
gözlemin içermediği bir trafiği. Karşılaştırma yine yapılır — en yakın gözlem odur — ama
`is_comparable=False` ile işaretlenir ve bir sınama sayılmaz.

**Yüklü-sefer ve yük-payı sütunları pratikte boş.** Yayında ikisi de var, türetme
ikisini de taşır, ama dolu satır sayısı dönem başına 22 / 13 / 9 ve 0 / 0 / 1. Yani
"deniz tarafının boş dönüşü" (yüklü sefer ile tüm seferler arası fark) bu veriyle
ölçülemiyor. Karşılaştırma tek dolu sütun olan **tüm seferler** üzerinden yapılır.

**Neden TTW ile karşılaştırılır.** MRV, geminin yaktığı yakıttan çıkan CO2'yi bildirir —
tanımı gereği tank-to-wake. GLEC'in 0,068'lik WTW değeri ayrıca yakıtın üretim ve
taşınma emisyonunu taşır; hiçbir gemi bunu bildirmez, hiçbir doğrulayıcı denetlemez.
Karşılaştırma bu yüzden **GLEC TTW = 0,063** ile yapılır.

**Ham çalışma kitapları depoya konmaz** (yılda ~25 MB, ayrıca yeniden dağıtımı bize ait
değil). Türetilmiş CSV konur — kanıt odur — ve `import_mrv.py` onu yeni bir indirmeden
yeniden üretir.

### Ne bulundu

Üç dönemin üçünde de aynı tabloyu veriyor (kg CO2/ton-km, TTW):

| Dönem | Gemi | Medyan | Orta yarı (Ç1–Ç3) | Genişlik |
|---|---|---|---|---|
| 2023 | 225 | 0,0588 | 0,0393 – 0,1056 | 2,7× |
| 2024 | 225 | 0,0538 | 0,0387 – 0,0986 | 2,6× |
| 2025 | 234 | 0,0508 | 0,0360 – 0,0957 | 2,7× |

**1 — GLEC'in ro-ro faktörü tarif ettiği filonun içinde duruyor.** 0,063 her üç dönemde
de orta yarının içine düşüyor (medyanın 1,07× / 1,17× / 1,24× katı, filonun ~%55'i
altında). Bir filo ortalamasının hiçbir gemiye eşit olması beklenmez; adil bir orta
olması beklenir ve bu değer onu sağlıyor. Motorun bütün multimodal rakamı bu faktöre
dayandığı için, dışarıdan onaylanmış tek doğrulama buydu.

**2 — Ama faktörün raf ömrü var.** Gözlenen medyan üç dönemde %14 düştü; sabit bir
2022 faktörü her yıl gerçeğin biraz daha üstünde kalıyor.

**3 — Asıl bulgu, ortalamanın gizlediği yayılım.** Orta yarı 2,7 kat aralığa yayılıyor:
aynı seferi taşıyan iki doğrulanmış gemi arasında bu kadar fark var. Hangi filo
ortalaması seçilirse seçilsin tek bir geminin gerçeğini veremez. Bu, motorun değil
yöntemin sınırı — ve raporlanmadığında sayı hak etmediği bir kesinlik taşır.

**4 — Doğrulama veri setinin kendi deniz faktörü filonun tamamının altında.** Motorun
`reference` seti yayımlanmış bir standart değil; gerçek bir müşteri karbon raporunun
kullandığı 0,012 değeri. Faktör tablosu başından beri "ro-ro servisi olmasına rağmen
konteyner gemisi değerine yakın" notunu taşıyordu. MRV bunu şüpheden gözleme
çeviriyor: dönemdeki **234 doğrulanmış ro-ro gemisinin hiçbiri** o kadar temiz değil
(medyanın 0,24 katı). Bu esasla fiyatlanan bir rapor deniz ayağını yaklaşık dört kat
eksik gösteriyor.

---

# Faz 9.0 — kaynak fizibilitesi

Bu bölüm veri değil, **kaynak testi** kaydı. Projede Faz 0 aynı işi yapmış ve işe
yaramıştı (searoute'un Korint hatası ve PortWatch'un yanlış adresi orada çıkmıştı), o
yüzden ağ ve demiryolu işine başlamadan önce her aday kaynak *indirilmeyi deneyerek*
sınandı. Çıkmaz çıkanlar da burada: bulunamadığını yazmak, aramamış gibi yapmaktan iyidir.

Test tarihi: **26 Ağustos 2026.**

## ERA RINF — AÇIK, kimlik doğrulaması yok ✅

Avrupa Birliği Demiryolu Ajansı'nın **Altyapı Kaydı**: 2019/777 sayılı Uygulama
Tüzüğü'nün zorunlu kıldığı, altyapı işletmecilerinin kendi bildirdiği resmî kayıt.

**Adres bulmak işin kendisiydi**, çünkü belgelenen yollar kapalı:

| Denenen | Sonuç |
|---|---|
| `data-interop.era.europa.eu` (topluluk projelerinin işaret ettiği) | **DNS'te yok** — adres ölmüş |
| `rinf.data.era.europa.eu/sparql` | JavaScript uygulaması, API değil |
| `rinf.data.era.europa.eu/api/v1/openapi.json` | **401** — belgelenen API kimlik istiyor |
| **`graph.data.era.europa.eu/repositories/rinf-plus`** | **200, açık, 47,4 milyon üçlü** |

Sonuncusu, RINF web uygulamasının kendi `main.js` paketi okunarak bulundu — THETIS-MRV'de
de aynı şey yapılmıştı. GraphDB deposu, standart SPARQL, anahtar gerekmiyor.

**İçinde bu projenin ihtiyaç duyduğu her şey var:**

| Ne | Sayı / alan |
|---|---|
| `OperationalPoint` (düğüm) | 60.571 — `opName`, `uopid`, `opType`, `inCountry` |
| Koordinat | `geosparql:hasGeometry` → `asWKT`, ör. `POINT(18.7305 45.23)` |
| `SectionOfLine` (kenar) | 69.457 — `opStart`, `opEnd`, **`lengthOfSectionOfLine`** (km) |
| Hız | `Track` üstünde **`maximumPermittedSpeed`** |
| TEN-T | `TENTCorridor` 176.761, `TENCorridor` 113.995 |

Yani **resmî uzunluklarla rotalanabilir bir demiryolu grafiği** ve gerçek izin verilen
hız. `schedule.py`'deki `RAIL_SPEED_KMH = 40.0` sabitinin yerine ölçülmüş değer koyulabilir.

Projenin kendi terminalleri arandı, dördü de bulundu:

```
AT01080  Wels Vbf                  IT03471  TRIESTE CAMPO MARZIO
DE95937  Duisburg Hbf              CZ34414  Ostrava-Kuncice
```

**Bilinen sınır — 27 ülke, Türkiye ve Sırbistan yok.** RINF bir AB/AEA kaydı; Türkiye
altyapı işletmecisi bildirim yapmıyor. Almanya 23.932, Fransa 13.443, Çekya 3.905,
İtalya 3.640, Romanya 2.329, Avusturya 1.489, Bulgaristan 350 kesim — ama `TUR` ve `SRB`
hiç yok.

Pratik sonucu net: mevcut 7 demiryolu bacağının **6'sı** (Trieste çıkışlı hepsi) resmî
kayıttan kaynaklandırılabilir; **Halkalı–Chitila** bacağının Türkiye ayağı kaynaklanamaz.
Bu, Eurostat boş dönüş verisindeki durumun birebir aynısı — ve çözümü de aynı olacak:
ikame etmek değil, **kapsamı ölçüp yazmak.**

## UN/LOCODE — indirilebilir, ama kaynağı netleştirilmeli ⚠️

Liman ve terminal kodları, koordinatlarıyla; `Function` sütunu bir yerin liman mı,
demiryolu terminali mi, karayolu terminali mi olduğunu söylüyor — terminal
kaynaklandırması için doğrudan uygun.

İki kopya da indi, **ama boyutları tutmuyor**: `datasets/un-locode` (GitHub) 2,0 MB,
`datahub.io` 7,3 MB. İkisi de UNECE'nin kendisi değil, topluluk aynası. Bu projenin
kuralı gereği kullanılmadan önce UNECE'nin kendi yayınına bağlanması ve sürümün
sabitlenmesi gerekiyor. **Faz 9.1'in ilk işi bu.**

## TEN-T / TENtec — çözülmedi ⏳

`ec.europa.eu/transport/infrastructure/tentec/tentec-portal/api/` → **404**.
`data.europa.eu` arama API'si çalışıyor (200, JSON), yani veri kümesi oradan aranabilir
ama hangi katmanın indirilebilir olduğu henüz bilinmiyor. RINF zaten `TENTCorridor`
taşıdığı için bu, muhtemelen ayrı bir kaynağa gerek bırakmayacak.

## OpenStreetMap / Overpass — yedek plana düştü ⏳

Çağrı biçimim yüzünden 406 döndü, kapalı olduğu anlamına gelmiyor. Ama RINF açık çıktığı
için OSM artık **10b yedeği**: resmî kayıt varken kalabalık kaynaklı veriyi esas almanın
gerekçesi yok. Türkiye ayağı için tekrar gündeme gelebilir — RINF'in görmediği tek yer orası.

## Deniz mesafesine hakem — henüz aranmadı ⏳

Faz 11. Çıkmaz çıkması da geçerli bir sonuç sayılacak.

---

## rinf_rail_graph.json + rail_distances_rinf.csv — ÇALIŞIYOR AMA SONUÇLARI KULLANILMAZ

| | |
|---|---|
| **Kaynak** | ERA RINF, `graph.data.era.europa.eu/repositories/rinf-plus` (açık SPARQL) |
| **Türetme** | `python scripts/import_rinf.py` (`--fetch` grafiği indirir, `--check` doğrular) |
| **Kapsam** | 12 koridor ülkesi · 47.198 hat kesimi · hepsi uzunluk taşıyor |
| **Durum** | **6/7 bacak için yol bulundu, 0 tanesi makul.** Aşağıya bakın. |

Zincirin her halkası çalışıyor: grafik iniyor, `uopid` ile birleşiyor (47.198/47.198),
terminaller işletme noktalarına bağlanıyor, en kısa yol hesaplanıyor. **Ama çıkan
mesafeler demiryolunun değil.**

```
trieste->wels   elle 420 km | RINF 748,7 km | +78,3%   IT SI HU SK AT
```

Yol İtalya'dan Slovenya, Macaristan ve Slovakya üzerinden Avusturya'ya gidiyor —
Tarvisio'dan doğrudan geçiş grafikte yok. 182 kesimlik bu yolun her parçası gerçek, ama
bütünü Alpler'in etrafından dolaşıyor. Grafikte **94 ayrı bileşen** ve ortalama **2,19
derece** var; bir demiryolu ağı için fazla seyrek. Eksik olan sınır bağlantıları.

Bu yüzden hiçbir satır `ok` demiyor: geçtiği ülke sayısı makul sınırı aşan her satır
`supheli: sinir sapmasi` olarak işaretleniyor ve bir test bunu sabitliyor. **Mevcut elle
yazılmış mesafeler yerinde kalıyor** — türetilenler onlardan daha iyi olduğu
kanıtlanana kadar değiştirilmeyecek.

### İki ek sınır

**Koordinat eşleştirmesi bu koridorda çalışmıyor.** RINF 60.571 işletme noktasının
yaklaşık 2.700'ü için konum yayımlıyor, ve koridor ülkelerinde konumu olan 1.415 noktanın
**1.401'i Avusturya'da**. Almanya, İtalya, Çekya ve Romanya hiç yayımlamıyor. İlk deneme
Wels ile Lambach'ı doğru bağladı, diğer her terminali 100–500 km uzaktaki bir Avusturya
istasyonuna bağladı.

Bunun yerine terminaller `data/rinf_terminal_map.csv` ile **adla ve elle** bağlanıyor.
Bu bir karar, ve karar gerekçesiyle, reddedilen alternatifleriyle birlikte dosyada
duruyor. Elle yazılmış mesafeden farkı şu: uçları insan seçiyor, **aradaki kilometreleri
kayıt veriyor.**

**İki uç doğrulanmamış.** Köln Eifeltor ve Duisburg'un intermodal terminali ada göre
bulunamadı; ikisi de şehrin ana garına bağlandı ve satırlarda `is_verified_choice=no`
yazıyor. O mesafeler "terminale" değil "şehre" okunmalıdır.
