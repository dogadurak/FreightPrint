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
