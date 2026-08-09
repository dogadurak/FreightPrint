# FreightPrint — durum ve devir notu

Bu belge projeyi başka bir asistanla sürdürecek biri için yazıldı. Kodun anlattığını
tekrar etmez; **kodda görünmeyen kararları, bulguları ve tuzakları** anlatır.

Son durum: **31 commit, 212 test geçiyor, çalışma ağacı temiz.**

---

## 1. Proje ne yapıyor

Kapıdan kapıya multimodal (karayolu / deniz / demiryolu) sevkiyat için **karbon, rota,
süre, maliyet ve risk** hesaplayan bir motor + web panosu. Pilot koridor
Gebze → Düsseldorf; deniz bacağı Pendik → Trieste ro-ro.

Ayrıntılı brifing: `PROJE_FreightPrint.md` (Türkçe, 552 satır).

### Ürünün asıl bulgusu — bunu kaybetmeyin

Müşterinin elindeki raporda deniz bacağı **0,012 kg CO2/ton-km** ile hesaplanmış. Bu bir
**konteyner gemisi** değeridir, ama servis **ro-ro**'dur. GLEC Framework 2019 (Tem 2022)
Tablo 45'e göre ro-ro treyler değeri **0,063** (refakatsiz) / **0,093** (refakatli).

Sonuç: müşterinin iddia ettiği **%83 tasarruf**, GLEC ile hesaplandığında **%19 ceza**ya
dönüşüyor. İşaret değişiyor.

> Bu yüzden ürün "tasarruf satmıyor", **hangi esasla hesaplandığını beyan ediyor.**
> Panoda faktör seti seçimi gizli bir varsayım değil, öne çıkan bir kontrol.
> Bu duruşu bozacak hiçbir "iyimser varsayım" eklemeyin.

---

## 2. Fazlar — ne bitti, ne kalmadı

| Faz | Konu | Durum |
|---|---|---|
| 0 | Fizibilite testi | ✅ Bitti |
| 1 | Çekirdek rota motoru | ✅ Bitti |
| 2 | Emisyon motoru | ✅ Bitti |
| 3 | Doğrulama | ✅ Bitti |
| 3.5 | Hata düzeltme turu (plan dışı, eklendi) | ✅ Bitti |
| 4 | API + web arayüzü | ✅ Bitti |
| 5 | Risk ve maliyet modülü | ✅ Bitti |
| — | Pano yükseltmesi, zaman ekseni, toplu iş kuyruğu, frigo, xlsx | ✅ Bitti |
| — | HVO / elektrik faktörleri (GLEC dışı kaynak) | ✅ Bitti |
| 6 | Terminal etki alanı (izokron) haritası | ❌ Hiç başlanmadı |
| 7 | AIS katmanı (koşullu) | ⛔ Faz 0'da elenmesi gerektiği görüldü |
| 8 | Paketleme / CI | ✅ Bitti |

### Faz 0 — Fizibilite (bitti)

Üç bulgu, üçü de sonraki her şeyi etkiledi:

- **searoute Korint Kanalı'ndan geçiyor.** Gerçek gemilerin geçemeyeceği kısayollar
  üretiyor ve `restrictions` parametresiyle engellenemiyor. Bu yüzden servis
  bacaklarında **referans mesafe esas alınır**; searoute yalnızca karşılaştırma için
  hesaplanır ve kanaldan geçen izler panoda kesikli çizilir + not düşülür.
- **PortWatch endpoint'i** düzeltildi (brifingdeki adres yanlıştı).
- **Akdeniz için ücretsiz AIS bir çıkmaz.** Faz 7 bu yüzden koşullu kalmalı; kapsam
  genişletmek isteyen önce veri kaynağını çözmeli.

### Faz 1–2 — Rota ve emisyon (bitti)

- OSRM (public demo sunucu; `OSRM_BASE_URL` ile kendi örneğinize çevirin)
- searoute + NetworkX ile multimodal graf
- GLEC tabanlı faktör seti sistemi, `data/emission_factors.csv`
- Faktörler **koda gömülü değil**; her satır kaynağını, yılını, kapsamını (TTW/WTW) ve
  `is_verified` bayrağını taşır. Doğrulanmamış faktör kullanılırsa çıktıya uyarı düşer.

### Faz 3 — Doğrulama (bitti)

- 34/34 tam karayolu satırı ve 19/22 multimodal satır **%1 içinde** yeniden üretiliyor
- Karayolu mesafe MAPE **%1,9** (34 satırın 30'u)
- Deniz bacağı: temiz rotalarda %4,2 sapma (n=1), Korint'ten geçenlerde %21,9 (n=5)
  — örneklem küçük, bu yüzden **kanıt değil, gösterge** olarak yazıldı

### Faz 4–5 — API, pano, risk, maliyet (bitti)

- FastAPI; `/api/routes` senaryoları tek istekte hesaplar, pano istemci tarafında anında
  geçiş yapar (yeniden rotalama yok — rotalama saniyeler, fiyatlama bedava)
- EU ETS denizcilik: %100 AEA içi, %50 AEA↔AEA dışı, kademeli geçiş 2024=%40 / 2025=%70 / 2026+=%100
- JWC Listed Areas (JWLA-033) risk poligonları, `data/risk_zones.geojson`
- Sapma senaryosu: Şanghay→Rotterdam Süveyş 19.538 km (3.385 km listeli alan içinde)
  vs Ümit Burnu 25.686 km (0 km) → +6.149 km, +9 gün, +9.300 kg CO2, +€372 ETS

### Plan dışı eklenenler (bitti)

- **Zaman ekseni**: kapıdan kapıya süre; yolda / aktarma / kalkış beklemesi ayrımı.
  EU 561/2006 şoför saatleri (9 sa sürüş, 4,5 saatte bir 45 dk mola, 11 sa günlük dinlenme).
  Gebze→Düsseldorf: karayolu 3,1 gün, multimodal 5,5 gün — farkın üçte biri elleçleme
  (18 sa) ve bekleme (25 sa).
- **Toplu rapor + arka plan iş kuyruğu**: 100 satır 36,6 sn → 9,4 sn (eşzamanlılık 4).
  500 satır seri ~50 dk sürerdi, yani her timeout'u aşardı.
- **Frigo (reefer)**: aşağıda ayrı başlık — türetme mantığı önemli.
- **xlsx yükleme** + Avrupa/Türkçe Excel CSV biçimleri (noktalı virgül, virgüllü ondalık).

---

## 3. Alternatif yakıtlar — neden GLEC satırından ölçekleniyor

GLEC'te HVO ve bataryalı elektrik **yok** (Tablo 42 yalnızca Diesel, CNG, LNG, Bio-LNG).
Bu yüzden dışarıdan gelmeleri gerekiyor — ama nasıl geldikleri kritik.

Bir kamyonun kWh/km'sini ve yükünü bağımsız varsayarsanız, GLEC dizel satırının ima
ettiğinden farklı bir yük esasına oturursunuz. Bu depoda tam olarak bu olmuştu: elektrik
satırları ~21 t, GLEC dizeli ise ~14 t ima ediyordu; elektrik yarı yarıya avantajlı
görünüyordu ve dosyada bunu söyleyen hiçbir şey yoktu.

Çözüm: **GLEC faktörünü DEFRA'nın litre başı dizel değerine bölün.** Çıkan sayı GLEC'in
kendi yakıt yoğunluğudur (0,02348 L/ton-km) — yük ve boş dönüş sadeleşir, türetilen
satırlar GLEC'in esasını *inşa gereği* devralır.

- **HVO**: aynı motor, aynı yük → litre oranı geçerli; HVO'nun düşük hacimsel enerjisi
  için düzeltilir (35,8 vs 34,4 MJ/L). Dizelin **%19'u**.
- **Elektrik**: farklı aktarma organı → verim oranı gerekli (0,375 tank→tekerlek,
  0,78 şebeke→tekerlek). Bu varsayım gömülü değil, `notes` içinde yazılı.

Şebeke sonucu belirliyor: İsveç dizelin %6'sı, **Polonya %94'ü** — yani orada bataryalı
kamyonun kazancı neredeyse yok. Tek bir "AB ortalaması" bu yüzden reddedildi.

Kaynaklandıramadığım iki şey **atıldı**: Türkiye şebeke faktörü ve HVO'nun atık/bitkisel
ayrımı (DEFRA tek jenerik değer veriyor). Standart iddiası taşıyan bir üründe kaynaksız
sayı, boşluktan kötüdür. HVO satırı bunun yerine besleme stoğu uyarısı taşıyor.

Elektrik satırları **üretimin kendi yakıt tedarik zincirini içermiyor**; bunu `notes`
açıkça söylüyor, yani dizel WTW ile karşılaştırmada elektrik lehine hafif eksik tahmindir.

---

## 4. Frigo kararı — neden oran değil, saat

Devralan kişi bunu değiştirmeye kalkabilir, o yüzden gerekçe burada.

GLEC reefer'ı **yalnızca konteyner gemisi** için ve **oran** olarak verir (Tablo 46:
kuru 76 → reefer 145 g CO2e/TEU-km, ≈1,9×). Bu oran başka moda **taşınamaz**:

| Mod | Kuru taban (g/ton-km) | Oranla (×1,9) | Mutlak ek (+6,9) |
|---|---|---|---|
| Konteyner gemisi | 7,6 | 14,5 | 14,5 ✓ |
| Ro-ro treyler | 68,0 | 129,9 ✗ | 74,9 |
| Karayolu 40t | 75,0 | 143,2 ✗ | 81,9 |

Konteyner gemisinin ton-km emisyonu düşük olduğu için soğutma onu ikiye katlıyor
görünür; ro-ro zaten bir mertebe yüksek, aynı ünite orada küçük bir ek olur. Oranı
ro-ro'ya uygulamak ek yükü **~9 kat** abartırdı — ve bizim koridor ro-ro.

Modlar arası taşınabilen şey ünitenin **enerji çekişi**, o da **zamana** bağlı. Bu yüzden:

```
221 g CO2e / ton / saat   ← data/reefer_factors.csv
```

Türetme zinciri (hepsi dosyada açık yazılı): 145−76 = 69 g/TEU-km → GLEC s.38'deki
10 t/TEU ile 6,9 g/ton-km → 32 km/sa gemi hızıyla 221 g/ton/saat.

Üç varsayım içeriyor, hiçbiri bu hâliyle yayınlanmadı → satır `is_verified=no` taşır,
arayüzde uyarı düşer, ve rakam **taşıma toplamına katılmaz, ayrı kalem** olarak durur.

**Kritik sonuç:** soğutma kapıdan kapıya sürenin **tamamına** işlenir — aktarma ve kalkış
beklemesi dâhil, çünkü kutu o saatlerde de fişte. Multimodalin 42,5 saatlik durağan
süresi soğutma faturasının üçte biri. Koridorda multimodalin cezası kuru yükte %5 iken
frigo yükte **%11**. Km bazlı bir model bunu sıfır görürdü.

---

## 5. Kalan işler

### 5.1 Faz 6 — terminal etki alanı (izokron) — **tek kalan faz**

Hiç başlanmadı. Hangi terminalin hangi bölgeyi kapsadığını gösteren izokron haritası.
OSRM'in `/table` servisi ile yapılabilir; public demo sunucu bunun için yetersiz kalır,
`docker compose --profile self-hosted up -d` ile kendi OSRM örneğinizi kaldırın.

### 5.2 Risk poligonlarının bağımsız doğrulanması (kısmen çözüldü)

`data/risk_zones.geojson` **elle sayısallaştırılmış basitleştirilmiş dikdörtgenler**.
JWC sınırlarıyla satır satır karşılaştırılmadı — JWLA-033 yalnızca PDF olarak yayımlanıyor,
makine okunur hâli yok.

Kısmen kapatıldı: her bölgenin iddia ettiği boğazı gerçekten içerip içermediğini sınayan
kalıcı bir test var. Bu test benim iki hatamı yakaladı (`southern_red_sea` Süveyş'i iddia
ediyordu ama Süveyş 30°N, bölgenin tavanı 20°N; `black_sea_ukraine` Boğaz'ı iddia
ediyordu). Yine de **tam doğrulama değil** — sınır çizgileri hâlâ yaklaşık.

---

## 6. Tekrarlanmaması gereken hatalar

Bu projede çıkan ve **her biri sessizce yanlış sayı üreten** hatalar. Kod değişikliği
yaparken bunlara dikkat:

1. **Faktör setleri arası sessiz düşüş.** `--factor-set glec --fuel diesel` istenip
   `reference` değeri dönüyordu, başlık yine "glec" diyordu. Artık `find_factor`
   **hata verir**, başka sete düşmez.
2. **Doluluk oranı çift sayımı.** GLEC karayolu satırı zaten %72 doluluk / %30 boş dönüş
   varsayıyor. Kullanıcının değerini üstüne uygulamak faktörü 1,81 katına çıkarıyordu.
   `basis_load_factor` / `basis_empty_share` sütunları bunun için var: önce yayıncının
   varsayımı **çıkarılır**, sonra kullanıcınınki uygulanır.
3. **Mesafeye göre sıralama/kırpma.** Uzun bir deniz bacağı, kısa bir karayolu
   bacağından **daha az** emisyon üretebilir. Pareto baskınlığı moda göre yapılır,
   ham km'ye göre değil.
4. **Feribot bacağının rakam çalması.** OSRM adımlarından ayrılan 40 km'lik feribot,
   2.500 km'lik deniz bacağının `duration_h` değerini alıyordu. Geriye eşleme tamamen
   kaldırıldı; veri artık `ResolvedLeg` üzerinde taşınıyor.
5. **Çakışan risk bölgelerinin iki kez sayılması.** `distance_in_zones_km` artık
   bölgelerin **birleşimini** kullanıyor.
6. **İç içe havuzlar.** İş kuyruğu (4) × rapor havuzu (4) = public OSRM'e aynı anda 16
   istek. Limit artık çağıranda değil, **istemcide**: `road.py` içinde `BoundedSemaphore`.
7. **Belirsizlik bandının kendi nokta tahminini dışlaması.** Band artık ortalanıyor.
8. **Faktör setine yakıt eklemek çıplak aramayı belirsizleştirir.** `find_factor(road)`
   birden çok satır bulunca -- doğru biçimde -- hata verir. Çözüm testleri yakıt adı
   verecek şekilde yeniden yazmak **değildi**; veri artık set ve mod başına bir satırı
   `is_default` ile işaretliyor. Alternatif yakıtlar opt-in: yeni bir satır eklemek
   mevcut bir raporun anlamını değiştiremez. Varsayılanı olmayan set (`placeholder`)
   hâlâ reddediyor.
9. **Testin totolojik olması.** Bir testin, hesabı yarıya indirseniz bile geçtiği ortaya
   çıktı. Değiştirildi. **Yazdığınız testin mutasyonu yakaladığını doğrulayın.**

---

## 7. Çalışma yöntemi — işe yaradı, sürdürün

- **Her fazdan sonra bağımsız bir doğrulama ajanı** koştu. Yukarıdaki hataların çoğu
  böyle bulundu. "Hata çok çıktı" demek yöntemin bozuk olduğu anlamına gelmiyor —
  tersine, hataların **yüzeye çıktığı** anlamına geliyor.
- **Her sayı kaynağını taşır.** Kaynağı olmayan sayı `PLACEHOLDER` olur ve uyarı üretir.
- **Belirsizlik gizlenmez.** Tahminse "tahmin" yazar, türetmeyse "türetme" yazar.
- **Test kırıldığında testi değil sebebi düzeltin.** Bu depoda bir kez, kırılan testleri
  geçecek şekilde yeniden yazan bir betik (`fix_tests.py`) üretildi. Test kırılması
  genelde tasarımda eksik bir kavrama işaret eder -- burada "varsayılan yakıt"
  kavramıydı. Assertion'ı gevşetmek o kavramı bulmanızı engeller.
- **Aynı depoda aynı anda iki ajan çalıştırmayın.** 8 Ağustos akşamı ile 9 Ağustos
  arasında depoya başka bir araç dokunmuş: HVO/elektrik satırları eklenmiş, `placeholder`
  seti silinmiş, testler kırık bırakılmıştı. Commit edilmemiş olduğu için hangi kararın
  kime ait olduğu ancak `git diff` ile çözülebildi. **Devretmeden önce commit alın.**

---

## 8. Gizlilik / güvenlik — dikkat

- `scripts/check_privacy.py` her commit öncesi koşuyor: gerçek müşteri şehir adları,
  posta kodları ve rakamları hazırlanmış diff'te arıyor. **Bu betiği devre dışı
  bırakmayın.** İki kez benim kendi sızıntımı yakaladı (test verisine gerçek müşteri
  şehri ve gerçek bir tasarruf rakamı koymuştum).
- `dogrulama_veriseti.csv`, `data/geocode_cache.json`, `data/route_cache.sqlite`,
  `osrm-data/` ve müşteri `.xlsx` dosyaları **gitignore'da kalmalı**, commit'e girmemeli.
- **Çözülmemiş bir veri konusu var ve sahibi kendisi ilgileniyor.** Git geçmişine,
  depo görünürlüğüne veya uzak dala dokunmayın: `filter-repo` çalıştırmayın, force-push
  yapmayın, bekleyen commit'leri push etmeyin. Bu, sahibinin açık talimatıdır.
- **26 commit push edilmemiş durumda** ve bu bilinçli.

---

## 9. Paketleme ve CI (Faz 8, bitti)

```bash
docker compose up -d app                      # public OSRM ile -> :8100
docker compose --profile self-hosted up -d    # yerel OSRM ile
python scripts/install_hooks.py               # gizlilik kancası
```

Üç şeyi bilin:

- **Testler ağa çıkmaz.** `conftest.py` her testin ağ erişimini kapatır. Bu koruma
  eklenirken Nominatim'e canlı istek atan bir test bulundu — CI'da rastgele kırılırdı.
  Takım 46 sn'den 17 sn'ye indi. Muafiyet `@pytest.mark.network`, şu an kullanan yok.
- **Önbellek `/app/var`, referans veri `/app/data`.** Bunları ayırmak zorunluydu: ikisi
  de `data/` altındayken, önbelleği korumak için oraya volume bağlamak faktör tablosunu
  da sabitler ve düzeltilmiş bir faktör yeniden dağıtılan konteynere ulaşmazdı.
  `FREIGHTPRINT_CACHE_DIR` ile taşınır.
- **İki ayrı gizlilik kontrolü var.** `check_privacy.py` doğrulama veri setini okur, o
  yüzden yalnızca yerelde çalışır. `check_tracked_files.py` yalnızca depoya bakar, o
  yüzden CI'da çalışan budur: hassas bir dosya takibe girmiş mi, ve bir defter
  **çıktısıyla** commit'lenmiş mi (defter kaynağı serbest, çıktısı değil).

CI dosyası `.github/workflows/ci.yml` — depo push edilene kadar koşmaz.

---

## 10. Çalıştırma

```bash
pip install -r requirements.txt
cd backend && python -m uvicorn app.main:app --port 8100 --reload
# pano: http://127.0.0.1:8100/
cd backend && python -m pytest -q      # 200 test
```

> Not: `--reload`'un çocuk süreci komut satırında `uvicorn` geçirmez. Sunucuyu
> öldürürken süreç filtresi buna takılıyor; port takılı kalırsa başka bir port deneyin.

Bilinen sınırların tam listesi `README.md` → "Bilinen sınırlar".
