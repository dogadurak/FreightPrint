# FreightPrint — Çok Modlu Yük Taşımacılığı Karbon ve Rota Analiz Motoru

> **Bu doküman ne işe yarar:** VS Code içindeki kodlama ajanına verilecek tam proje brifingidir.
> Projenin nereden çıktığını, neyi çözdüğünü, neyi çözmediğini, hangi veriyi kullanacağını,
> nasıl inşa edileceğini ve nasıl doğrulanacağını içerir. Ajan bu dokümanı okuduğunda
> ek soru sormadan Faz 1'e başlayabilmelidir.

---

## 1. Tek cümlelik tanım

Bir sevkiyatın kalkış ve varış noktasını girdiğinde, gerçek yol/deniz/demiryolu ağları üzerinden
çok modlu rota alternatiflerini hesaplayan; her alternatif için karbon salımını, süreyi, maliyeti ve
güzergâh risklerini karşılaştırmalı olarak sunan ve müşteriye teslim edilebilir karbon raporu üreten
açık kaynak sistem.

---

## 2. Proje nereden çıktı — saha doğrulaması

Bu proje masabaşı bir fikir değil; lojistik sektöründe çalışan iki kişiyle yapılan görüşmeler sonucu şekillendi.

### Görüşme 1 — deniz taşımacılığı tarafı

Sorulan: *"EU ETS karbon maliyeti sizin şirkette gerçekten göz önünde bulundurulan bir problem mi?
Ve maliyete göre aktarma limanı seçimi gerçekten yapılıyor mu?"*

Alınan cevaplar:

- ETS/karbon maliyetinin gerçek bir gündem olduğu doğrulandı.
- Maliyete göre liman/rota yönlendirme mantığının sektörde karşılığı olduğu doğrulandı.
- **Beklenmedik ve en değerli bilgi:** Kızıldeniz'deki saldırılar nedeniyle gemiler rota değiştiriyor,
  armatörler bunu "savaş surcharge" (war risk surcharge) olarak faturaya yansıtıyor ve şirket bu ek
  ücreti ödemek zorunda kalıyor.

**Projeye etkisi:** Rota değişikliğinin maliyetini hesaplayan ve riskli bölgeleri gösteren Modül B doğdu.

### Görüşme 2 — karayolu/intermodal taşımacılık tarafı

Sorulan: aynı sorular.

Alınan cevaplar:

- Büyük firmalar karbonu daha çok gözetiyor.
- Firmalar sadece deniz/liman değil, **TIR'ların karbon emisyon değerlerini de rapor olarak istiyor.**
- Dürüst uyarı: *"Sizin karbonunuz yüksek, sizinle çalışamayız"* gibi bir durumla hiç karşılaşmamış.
  Karbon şu an bir satın alma kriteri değil, daha çok "kâğıt üstünde" bir gereklilik.
- Buna rağmen: *"Bundan ilerleyebilirsin."*

**Projeye etkisi:** Karbon raporu bir ikna aracı değil, **zorunlu bir teslimat**. Yani talep
"firmaları karbonu azaltmaya ikna etmek" değil, "zaten üretmek zorunda oldukları raporu
otomatik ve doğru üretmek". Modül A'nın ana omurga olmasının sebebi budur.

Ayrıca bu kişi, kendi firmasının iki gerçek müşteri için hazırladığı karbon ayak izi raporunu paylaştı.
Bu raporlar sistemin **doğrulama veri seti** olarak kullanılacak (bkz. Bölüm 9).

---

## 3. Projenin duruşu — bu proje NE DEĞİL

Bu bölüm önemli. Ajan ve ileride README yazılırken bu çerçeve korunmalı.

| Bu proje… | Açıklama |
|---|---|
| ❌ Mevcut raporların hatasını bulan bir denetim aracı **değil** | Eldeki gerçek raporlar yalnızca sistem kurulduktan sonra, kendi hesabımızın tutarlılığını ölçmek için kullanılır. Dışarıya "şu firmanın raporu hatalı" şeklinde sunulmaz. |
| ❌ Klasik rota optimizasyonu (VRP/TSP) **değil** | Araç filosu rotalama yapmıyoruz. Var olan sınırlı sayıda gerçek servis alternatifini karşılaştırıyoruz. |
| ❌ Gerçek zamanlı gemi takip panosu **değil** | Canlı takip değil, planlama ve raporlama aracı. |
| ❌ "Dijital ikiz" **değil** | Sınırı belirsiz bir kavram; kapsam kayması yaratır. Kullanılmayacak. |
| ❌ Savaş/saldırı kaynaklı ek ücreti **ortadan kaldırmaz** | Bu maliyet dışsaldır. Sistemin yaptığı, bu maliyeti **görünür, hesaplanabilir ve doğrulanabilir** kılmaktır. Pazarlama dilinde "maliyeti azaltır" denmeyecek. |
| ✅ Bu proje şudur | Gerçek coğrafya üzerinden çalışan, çok modlu, parametrik bir karbon + maliyet + risk hesaplama motoru ve onun web arayüzü. |

---

## 4. Pilot koridor ve ağ modeli

### 4.1 Neden pilot koridor gerekli

Dünyanın herhangi iki noktası arasında çok modlu rota kurmak veri açısından imkânsıza yakındır.
Ama kritik gerçek şu: **TIR her yere gidebilir, gemi ve tren gidemez.** Deniz ve demiryolu
taşımacılığı sabit terminaller arasında, tarifeli servisler üzerinden yapılır.

Bu yüzden sistem şöyle kurulacak:

- **Deniz ve demiryolu bacakları:** Önceden tanımlı, sınırlı sayıda servis kenarı (edge). ~15 adet.
- **Karayolu bacakları:** Serbest. Kullanıcının girdiği herhangi bir noktadan en yakın terminale
  kadar anlık hesaplanır.
- **Rota seçimi:** Bu küçük graf üzerinde en uygun terminal kombinasyonunun bulunması.

Böylece kullanıcı istediği iki şehri seçebilir, ama sistem yalnızca gerçekten var olan servisleri kullanır.

### 4.2 Seçilen pilot koridor

**Türkiye ⇄ Avrupa intermodal koridoru.**

Gerekçe:
- Doğrulama veri setindeki 34 sevkiyatın tamamı bu koridorda.
- Türkiye merkezli olması hem hikâye hem erişim avantajı sağlıyor.
- Terminal sayısı sınırlı, OSM yol verisi bu bölgede güçlü.

### 4.3 Terminal listesi (başlangıç)

| Terminal | Ülke | Tip |
|---|---|---|
| Pendik | TR | Ro-Ro limanı |
| Yalova | TR | Ro-Ro limanı |
| Ambarlı | TR | Liman |
| Mersin | TR | Liman |
| Halkalı | TR | Demiryolu terminali |
| Trieste | IT | Ro-Ro + demiryolu hub'ı |
| Bari | IT | Ro-Ro limanı |
| Patras | GR | Ro-Ro limanı |
| Sète | FR | Ro-Ro limanı |
| Duisburg | DE | Demiryolu terminali |
| Köln | DE | Demiryolu terminali |
| Ostrava | CZ | Demiryolu terminali |
| Regensburg | DE | Demiryolu terminali |
| Wels / Lambach | AT | Demiryolu terminali |
| Chitila | RO | Demiryolu terminali |

### 4.4 Servis bacakları (graf kenarları — başlangıç değerleri)

Aşağıdaki mesafeler doğrulama veri setindeki firma tablosundan alınmıştır.
**Bunlar başlangıç referansıdır; sistem kendi mesafesini hesaplayacak ve bu değerlerle karşılaştıracaktır.**

| Kalkış | Varış | Mod | Referans km |
|---|---|---|---|
| Pendik | Trieste | Deniz | 2500 |
| Pendik | Bari | Deniz | 1755 |
| Pendik | Patras | Deniz | 1450 |
| Yalova | Sète | Deniz | 3100 |
| Mersin | Trieste | Deniz | 2750 |
| Trieste | Patras | Deniz | 1225 |
| Trieste | Wels | Demiryolu | 420 |
| Trieste | Lambach | Demiryolu | 380 |
| Trieste | Köln | Demiryolu | 950 |
| Trieste | Duisburg | Demiryolu | 1020 |
| Trieste | Ostrava | Demiryolu | 670 |
| Trieste | Regensburg | Demiryolu | 540 |
| Halkalı | Chitila | Demiryolu | 515 |

> Not: Trieste bu ağın merkez hub'ıdır. Deniz bacakları oraya gelir, demiryolu bacakları oradan dağılır.

---

## 5. Modüller

### Modül A — Karbon hesaplama ve raporlama (ANA OMURGA, v1)

Kullanıcı akışı:
1. Sevkiyat bilgisi girilir (kalkış, varış, tonaj, araç/yakıt tipi) — tek tek veya toplu dosya ile.
2. Sistem uygun çok modlu rota alternatiflerini bulur.
3. Her alternatif için bacak bacak mesafe ve emisyon hesaplanır.
4. Aynı sevkiyatın tam karayolu ile yapılması durumundaki emisyon hesaplanır (karşılaştırma temeli).
5. Tasarruf edilen CO2 ve buna karşılık gelen ağaç eşdeğeri çıkarılır.
6. Sonuç harita + tablo + indirilebilir rapor olarak sunulur.

Çıktı kalemleri:
- Mod bazında km ve kg CO2 (ön taşıma karayolu / ana taşıma deniz / demiryolu / son taşıma karayolu)
- Toplam CO2
- Tam karayolu senaryosu CO2
- Tasarruf edilen CO2
- Ağaç eşdeğeri
- Belirsizlik aralığı (bkz. 6.2)

### Modül B — Rota alternatifi, risk ve maliyet (v1.5)

Aynı rota motorunun farklı çıktısı. Kullanıcı iki rotayı karşılaştırır:

- Mesafe ve süre farkı
- Ek yakıt ve ek CO2
- Tahmini ETS maliyeti (yalnızca AB kapsamındaki bacaklar için)
- Güzergâhın riskli deniz bölgeleriyle kesişimi
- Kesişim varsa uygulanabilecek savaş risk ek ücreti (parametrik girilir)

**Risk katmanı tasarımı:** Riskli deniz alanları poligon olarak tutulur; hesaplanan her deniz bacağı
bu poligonlarla kesiştirilir. Bu sayede katman rotadan bağımsızdır — pilot koridor Türkiye–Avrupa
olsa bile Kızıldeniz/Süveyş senaryosu demo olarak gösterilebilir.

**Dürüstlük notu:** Bu modül maliyeti azaltmaz. Armatörden gelen ek ücretin karşılığını
hesaplayarak doğrulanabilir hâle getirir.

### Modül C — AIS ile gerçek seyir verisi (v2, opsiyonel)

Fizibilitesi önce test edilecek (bkz. Bölüm 11 riskler).

- Gerçek seyredilen deniz mesafesi (tablo değeri yerine)
- Gerçek transit süresi ve sapması → rota önerisine "güvenilirlik" kriteri ekler
- Terminal bekleme süresi ve bu sırada oluşan emisyon
- Hıza bağlı emisyon: gemi emisyonu hızla doğrusal değil, kabaca kübik artar. Sabit faktör yerine
  gerçek hız profili kullanmak sistemin en bilimsel katkısı olur.

---

## 6. Ek özellikler

### 6.1 Yakıt ve araç tipi seçeneği (v1)
Kullanıcı araç/yakıt seçer, emisyon anında değişir: dizel, HVO/biyodizel, elektrikli, LNG.
Referans tablodaki "elektrikli ise" sütunu yarım bırakılmış — bu tamamlanacak.

### 6.2 Belirsizlik aralığı (v1)
Tek bir kesin sayı yerine aralık verilir: örn. `1.280 – 1.520 kg CO2`.
Mesafe ve doluluk oranı belirsizliği Monte Carlo ile (birkaç yüz örnekleme) dağılıma çevrilir.
Sahte hassasiyetten (örn. `502,67999999 kg`) kaçınmak için sonuçlar anlamlı basamağa yuvarlanır.

### 6.3 Doluluk oranı ve boş dönüş (v1)
Referans tabloda tonaj her sevkiyatta sabit 24 alınmış. Gerçekte doluluk oranı emisyonu belirleyen
en büyük etkenlerdendir. Doluluk oranı ve boş dönüş yüzdesi parametre olarak modele girecek.

### 6.4 Terminal etki alanı haritası (v1.5)
Her terminal için sürüş süresine göre erişim alanı (isochrone) üretilir, harita renklendirilir.
"Bu fabrika hangi terminale bağlanmalı?" sorusunu cevaplar. Projenin en güçlü görsel çıktısı budur.

### 6.5 Servis takvimi (v2)
Gemi haftada 3 gün kalkıyorsa kaçırılan sefer 2 gün bekleme demektir. Toplam transit süresini
gerçekte belirleyen budur. "En hızlı rota" kriterini anlamlı kılar.

### 6.6 Versiyonlu emisyon faktörü kütüphanesi (v1)
Faktörler koda gömülmeyecek; kaynağı, yılı ve kapsamı (TTW/WTW) belli bir tabloda tutulacak.
Rapor çıktısında "bu hesap hangi faktör setiyle üretildi" bilgisi yer alacak.

### 6.7 Veri kalitesi skoru (v1.5)
Her sevkiyat için "birincil veri mi, tahmin mi" etiketi. ISO 14083 bunu ister.
Rapor sonunda "verilerin %X'i birincil kaynaklı" ifadesi raporu ciddileştirir.

---

## 7. Hesaplama metodolojisi

### 7.1 Temel formül

```
bacak_emisyonu (kg CO2) = mesafe_km × taşınan_ton × emisyon_faktörü (kg CO2 / ton-km)

toplam_emisyon = Σ (her bacağın emisyonu)

tasarruf = tam_karayolu_emisyonu − toplam_emisyon
```

### 7.2 Referans emisyon faktörleri (başlangıç)

Doğrulama veri setindeki firmanın kullandığı değerler:

| Mod | kg CO2 / ton-km |
|---|---|
| Karayolu | 0,121 |
| Deniz | 0,012 |
| Demiryolu | 0,016 |

> ⚠️ **Doğrulanması gereken nokta:** Bu değerler büyük olasılıkla TTW (tank-to-wheel) kapsamındadır.
> ISO 14083 / GLEC Framework ise WTW (well-to-wake) ister; WTW değerleri belirgin şekilde daha yüksektir.
> Sistem her iki kapsamı da destekleyecek ve hangisinin kullanıldığını raporda belirtecek.

### 7.3 Ağaç eşdeğeri dönüşümü

Referans tabloda kullanılan katsayılar:

| Katsayı | Değer (kg CO2 / ağaç / yıl) |
|---|---|
| Ortalama ağaç | 22,5 |
| Kızılçam (Pinus brutia) | 411,4 |

```
ağaç_sayısı = tasarruf_edilen_kg_CO2 / katsayı
```

> ⚠️ Bu katsayılar **koda gömülmeyecek, ayarlanabilir parametre olacak.** Referans raporlarda
> aynı hektar değeri farklı ağaç sayıları için tekrar ettiğinden, hektar dönüşümü ağaç sayısından
> türetilecek şekilde kurulmalıdır.

### 7.4 ETS maliyeti (Modül B)

```
ets_maliyeti (€) = AB_kapsamındaki_emisyon (ton CO2) × kapsam_oranı × karbon_fiyatı (€/ton)
```

Kapsam oranı kademelidir: 2024 için %40, 2025 için %70, 2026 ve sonrası için %100.
Karbon fiyatı parametre olarak girilir (piyasa fiyatı değişkendir).

---

## 8. Veri kaynakları

### 8.1 Doğrudan kullanılacaklar

| Kaynak | Ne için | Erişim | Not |
|---|---|---|---|
| **OpenStreetMap** | Karayolu ağı, mesafe ve süre | `osmnx`, veya kendi OSRM sunucun | Ücretsiz, ODbL lisans. Yoğun kullanım için kendi OSRM örneğini Docker ile çalıştır. |
| **searoute-py** | Deniz rotası ve mesafesi | PyPI paketi | Karada düz çizgi yerine gerçek deniz güzergâhı üretir. |
| **TEN-T / GISCO** | AB ulaştırma ağı, demiryolu hatları | https://ec.europa.eu/eurostat/web/gisco/geodata | Ücretsiz shapefile. |
| **OpenRailwayMap** | Demiryolu geometrisi | OSM tabanlı | Demiryolu bacağı doğrulaması için. |
| **World Port Index (NGA)** | Liman konumları ve öznitelikleri | https://data.humdata.org/dataset/world-port-index | 3.700+ liman, ücretsiz. |
| **IMF PortWatch** | Liman çağrı sayısı, chokepoint transit verisi | https://portwatch.imf.org | Günlük, ücretsiz, kayıt yok. ArcGIS FeatureServer API + CSV indirme. Modül B'de rota kayması doğrulaması için. |
| **EMODnet Human Activities** | Gemi yoğunluğu (AB suları, Akdeniz dahil) | https://emodnet.ec.europa.eu/en/human-activities | 1×1 km aylık GeoTIFF, ücretsiz ve kısıtsız. Rota koridoru görselleştirmesi için. |
| **THETIS-MRV (EMSA)** | Gemi bazlı yıllık CO2 (AB'ye uğrayan gemiler) | https://mrv.emsa.europa.eu | Ücretsiz. Deniz emisyon faktörünü kalibre etmek ve doğrulamak için. |
| **GLEC Framework / ISO 14083** | Emisyon faktörü ve metodoloji çerçevesi | Smart Freight Centre | Hesabın hangi standarda dayandığını belirtmek için. |

### 8.2 Fizibilitesi önce test edilecekler

| Kaynak | Ne için | Risk |
|---|---|---|
| **Ham AIS (Akdeniz/Adriyatik)** | Modül C: gerçek mesafe, süre, hız, bekleme | ⚠️ Ücretsiz gemi bazlı AIS verisinin coğrafi kapsamı düzensiz. Danimarka (`aisdata.ais.dk`) ve ABD (`marinecadastre.gov`, CC0) için mükemmel açık veri var; Akdeniz için sınırlı. **Projeye başlamadan test edilmeli.** |
| **Savaş riskli deniz bölgeleri** | Modül B: risk poligonları | ⚠️ Sigorta piyasasının ilan ettiği bölgeler kamuya açık duyurulur, ancak prim oranları pazarlıkla belirlenir ve açık veri değildir. Poligonlar elle sayısallaştırılabilir; prim oranı **kullanıcı girdisi** olarak tasarlanmalı. |
| **Sınır kapısı bekleme süreleri** | Kapıkule vb. kuyruk süresi ve rölanti emisyonu | ⚠️ Düzenli ve güvenilir açık veri bulmak zor. Veri kaynağı doğrulanmadan kapsama alınmayacak. |

---

## 9. Doğrulama planı

Sistem kurulduktan **sonra** yapılacak iç doğrulama adımıdır. Projenin satış hikâyesi değildir.

### 9.1 Doğrulama veri seti

`dogrulama_veriseti.csv` dosyası bu dokümanla birlikte verilmiştir. İçeriği:

- **34 gerçek sevkiyat satırı**, iki müşteri raporundan çıkarılmıştır (`MUSTERI_A`, `MUSTERI_B`).
- Sütunlar: yükleme/boşaltma ülke ve şehir, servis rotası, araç tipi, tonaj,
  bacak bazında mesafeler (ön taşıma karayolu, deniz, demiryolu, son taşıma karayolu),
  tam karayolu mesafesi, toplam CO2, tam karayolu CO2, tasarruf edilen CO2.

> 🔒 **Gizlilik:** Bu veri gerçek müşteri ve taşıyıcı bilgisi içerir. Dosyadaki müşteri adları
> anonimleştirilmiştir, ancak rota ve firma bilgileri hâlâ hassastır.
> **Bu dosya herkese açık bir depoya yüklenmeyecektir.** Yerel doğrulama için kullanılacak,
> `.gitignore` içine alınacaktır. Sonuçlar "gerçek bir lojistik firmasının anonimleştirilmiş
> sevkiyat kayıtları" olarak raporlanacaktır.

### 9.2 Ölçülecek metrikler

1. **Mesafe tutarlılığı:** Sistemin hesapladığı bacak mesafeleri ile referans tablodaki değerler
   arasındaki sapma (ortalama mutlak yüzde hata).
2. **Emisyon tutarlılığı:** Aynı emisyon faktörleri kullanıldığında sistemin ürettiği toplam CO2
   ile referans değerin farkı. Aynı faktörle fark neredeyse sıfır olmalıdır — bu, hesap
   mantığının doğruluğunu kanıtlar.
3. **Farkın kaynağı:** Sapmaların mesafeden mi, doluluk varsayımından mı, faktör kapsamından mı
   (TTW/WTW) geldiğinin ayrıştırılması.

### 9.3 Başarı eşiği

- Aynı faktör ve aynı mesafe girildiğinde emisyon farkı **< %1** olmalı (hesap mantığı doğru).
- Sistem kendi hesapladığı mesafelerle çalıştığında sapma raporlanabilir ve açıklanabilir olmalı.

---

## 10. Teknik mimari

### 10.1 Katmanlar

```
┌─────────────────────────────────────────────┐
│  Web arayüzü (harita + form + rapor)        │
└─────────────────┬───────────────────────────┘
                  │ REST
┌─────────────────▼───────────────────────────┐
│  FastAPI servis katmanı                     │
│  /route  /calculate  /report  /risk         │
└─────────────────┬───────────────────────────┘
                  │
┌─────────────────▼───────────────────────────┐
│  Çekirdek: Çok modlu rota motoru            │
│  graf kurulumu • en uygun kombinasyon       │
└──┬──────────────┬──────────────┬────────────┘
   │              │              │
┌──▼────────┐ ┌───▼────────┐ ┌───▼──────────┐
│ Karayolu  │ │ Deniz      │ │ Demiryolu    │
│ OSM/OSRM  │ │ searoute   │ │ TEN-T/OSM    │
└───────────┘ └────────────┘ └──────────────┘
                  │
┌─────────────────▼───────────────────────────┐
│  PostgreSQL + PostGIS                       │
│  terminaller • servis bacakları • risk poly │
│  emisyon faktörleri • sevkiyat kayıtları    │
└─────────────────────────────────────────────┘
```

### 10.2 Teknoloji yığını

| Katman | Seçim | Gerekçe |
|---|---|---|
| Dil | Python 3.11+ | Mevcut yetkinlik |
| API | FastAPI | Async, otomatik OpenAPI dokümantasyonu, Pydantic doğrulama |
| Mekânsal işlem | GeoPandas, Shapely, pyproj | Standart CBS yığını |
| Yol rotalama | OSRM (Docker) veya `osmnx` + NetworkX | OSRM daha hızlı; osmnx prototip için yeterli |
| Deniz rotası | `searoute` | Gerçek deniz güzergâhı |
| Graf | NetworkX | 15 kenarlık ağ için fazlasıyla yeterli |
| Veritabanı | PostgreSQL + PostGIS | Mekânsal sorgu, poligon kesişimi |
| Önbellek | Redis | Tekrarlanan rota sorguları için |
| Frontend | React + MapLibre GL (veya Leaflet) | Ücretsiz, vektör harita |
| Rapor çıktısı | Excel (openpyxl) + PDF | Müşteriye teslim formatı |
| Paketleme | Docker + docker-compose | Tek komutla ayağa kalkma |
| Test | pytest | Doğrulama testleri dahil |
| CI | GitHub Actions | Test + lint |

### 10.3 Önerilen dizin yapısı

```
freightprint/
├── docker-compose.yml
├── README.md
├── data/
│   ├── terminals.geojson          # terminal noktaları
│   ├── service_legs.csv           # deniz/demiryolu servis kenarları
│   ├── risk_zones.geojson         # riskli deniz bölgeleri
│   └── emission_factors.csv       # versiyonlu faktör tablosu
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI giriş
│   │   ├── api/
│   │   │   ├── routes.py          # rota uçları
│   │   │   ├── calculate.py       # emisyon hesabı uçları
│   │   │   └── reports.py         # rapor üretimi
│   │   ├── core/
│   │   │   ├── network.py         # çok modlu graf kurulumu
│   │   │   ├── road.py            # OSRM/osmnx sarmalayıcı
│   │   │   ├── sea.py             # searoute sarmalayıcı
│   │   │   ├── rail.py            # demiryolu bacağı
│   │   │   ├── emissions.py       # faktör uygulama, WTW/TTW
│   │   │   ├── uncertainty.py     # Monte Carlo aralık
│   │   │   ├── risk.py            # poligon kesişimi, surcharge
│   │   │   └── cost.py            # ETS ve ek ücret hesabı
│   │   ├── models/                # Pydantic şemaları
│   │   └── db/                    # PostGIS bağlantısı, migration
│   └── tests/
│       ├── test_emissions.py
│       ├── test_network.py
│       └── test_validation.py     # 34 sevkiyatla doğrulama
├── frontend/
│   └── src/
│       ├── components/Map.jsx
│       ├── components/ShipmentForm.jsx
│       ├── components/ResultPanel.jsx
│       └── components/RiskLayer.jsx
└── notebooks/
    └── validation_analysis.ipynb  # doğrulama sonuç analizi
```

### 10.4 Temel API uçları

| Uç | Metot | İşlev |
|---|---|---|
| `/api/terminals` | GET | Terminal listesi |
| `/api/route` | POST | Kalkış+varış → çok modlu rota alternatifleri |
| `/api/calculate` | POST | Rota + tonaj + araç → emisyon, aralık, ağaç eşdeğeri |
| `/api/compare` | POST | İki rotayı karşılaştır (Modül B) |
| `/api/risk` | POST | Rotanın risk bölgeleriyle kesişimi ve ek ücret tahmini |
| `/api/report` | POST | Toplu sevkiyat dosyası → indirilebilir rapor |
| `/api/catchment/{terminal_id}` | GET | Terminal etki alanı (isochrone) |

### 10.5 Veri modeli (özet)

```
terminals(id, name, country, type, geom)
service_legs(id, from_terminal, to_terminal, mode, ref_distance_km,
             computed_distance_km, frequency_per_week, geom)
emission_factors(id, mode, vehicle_type, fuel_type, scope, value, unit, source, year)
risk_zones(id, name, zone_type, valid_from, valid_to, geom)
shipments(id, origin, destination, tonnage, vehicle_type, fuel_type, created_at)
shipment_legs(id, shipment_id, mode, distance_km, co2_kg, factor_id)
```

---

## 11. Yapılış aşamaları

Her fazın sonunda **gösterilebilir bir çıktı** olacak. Bu, projenin yarım kalmaması için kritik.

### Faz 0 — Fizibilite testi (1–2 gün)
- [ ] `searoute` ile Pendik–Trieste mesafesini hesapla, referans 2500 km ile karşılaştır
- [ ] OSRM'i Docker'da ayağa kaldır veya `osmnx` ile Gebze–Trieste karayolu mesafesini al
- [ ] PortWatch API'sinden tek bir chokepoint için veri çek
- [ ] Akdeniz için ücretsiz gemi bazlı AIS erişimini test et → **Modül C'nin kaderi buna bağlı**
- **Çıktı:** Hangi veri kaynağının çalıştığını gösteren kısa bir notebook

### Faz 1 — Çekirdek rota motoru (1 hafta)
- [ ] Terminal ve servis bacağı verisini oluştur
- [ ] NetworkX ile çok modlu graf kur
- [ ] Karayolu bacağını serbest hesaplayan sarmalayıcıyı yaz
- [ ] "İki nokta → rota alternatifleri" fonksiyonu çalışsın
- **Çıktı:** Komut satırından çalışan, rota döndüren bir fonksiyon

### Faz 2 — Emisyon motoru (1 hafta)
- [ ] Versiyonlu emisyon faktörü tablosu
- [ ] Bacak bazında hesap, toplam, tam karayolu karşılaştırması
- [ ] Ağaç eşdeğeri (parametrik)
- [ ] Yakıt/araç tipi seçeneği
- [ ] Belirsizlik aralığı (Monte Carlo)
- **Çıktı:** Tek sevkiyat girdisiyle tam emisyon çıktısı üreten modül

### Faz 3 — Doğrulama (3–4 gün)
- [ ] 34 sevkiyatı sisteme geçir
- [ ] Aynı faktör + aynı mesafe ile emisyon farkını ölç (< %1 hedefi)
- [ ] Kendi hesapladığın mesafelerle sapmayı ölç ve kaynağını ayrıştır
- **Çıktı:** `validation_analysis.ipynb` — grafikli, sayısal doğrulama raporu

### Faz 4 — API + web arayüzü (1,5 hafta)
- [ ] FastAPI uçları
- [ ] Harita üzerinde rota çizimi
- [ ] Sevkiyat formu ve sonuç paneli
- [ ] Toplu dosya yükleme ve rapor indirme
- **Çıktı:** Tarayıcıda çalışan, gösterilebilir uygulama — **projenin bitmiş sayılabileceği nokta**

### Faz 5 — Risk ve maliyet modülü (1 hafta)
- [ ] Risk poligonlarını sayısallaştır
- [ ] Deniz bacağı × risk bölgesi kesişimi
- [ ] Rota karşılaştırma ekranı
- [ ] ETS ve ek ücret hesabı (parametrik)
- **Çıktı:** Rota karşılaştırma sekmesi

### Faz 6 — Terminal etki alanı haritası (3–4 gün)
- [ ] Isochrone üretimi
- [ ] Harita katmanı
- **Çıktı:** Projenin tanıtım görseli

### Faz 7 — AIS katmanı (koşullu, Faz 0 sonucuna bağlı)
- [ ] Gerçek mesafe ve süre
- [ ] Hıza bağlı emisyon
- [ ] Terminal bekleme süresi

### Faz 8 — Paketleme
- [ ] Docker compose ile tek komutla ayağa kalkma
- [ ] README: problem, yaklaşım, doğrulama sonucu, ekran görüntüleri
- [ ] GitHub Actions ile test

---

## 12. Riskler ve önlemler

| Risk | Etki | Önlem |
|---|---|---|
| Akdeniz için ücretsiz AIS bulunamaması | Modül C düşer | Faz 0'da test edilir. Modül C hiçbir zaman v1'in içinde değil — bulunamazsa proje yine bitmiş olur. |
| Savaş primi oranlarının açık veri olmaması | Modül B'nin maliyet kalemi zayıflar | Prim oranı kullanıcı girdisi olarak tasarlandı. Sistem oranı değil, oranın uygulanacağı **mesafe ve süre farkını** hesaplıyor. |
| Kapsam kayması (sürekli yeni özellik eklenmesi) | Proje bitmez — önceki projede yaşanan sorun | Faz 4 sonunda proje "bitmiş" sayılır. Sonraki fazlar bonus. Yeni fikir gelirse v2 listesine yazılır, v1'e eklenmez. |
| OSRM kurulumunun karmaşıklığı | Faz 1 gecikir | Prototipte `osmnx` ile başla, performans sorun olursa OSRM'e geç. |
| Emisyon faktörü kapsamı karışıklığı (TTW/WTW) | Sayılar tartışmalı olur | Faktör tablosunda kapsam sütunu zorunlu; raporda açıkça belirtilir. |
| Gizli müşteri verisinin yanlışlıkla yayınlanması | Ciddi güven sorunu | Doğrulama CSV'si `.gitignore`'a alınır; depoda yalnızca sentetik örnek veri bulunur. |

---

## 13. Ekli dosyalar

| Dosya | İçerik |
|---|---|
| `dogrulama_veriseti.csv` | Gerçek raporlardan çıkarılmış 34 sevkiyat satırı. Yerel doğrulama için, depoya yüklenmeyecek. |

---

## 14. Ajan için özet talimat

> Bu projede Faz 0 ve Faz 1'den başla. Önce veri kaynaklarının erişilebilirliğini test et,
> sonra çok modlu rota motorunu kur. Emisyon hesabına, doğrulanabilir bir rota motoru
> çalışmadan geçme. Yeni özellik önerilerini v2 listesine yaz, v1 kapsamına ekleme.
> Kod Türkçe yorumlanabilir ama değişken ve fonksiyon adları İngilizce olsun.
> Her fazın sonunda çalışan bir çıktı üret; yarım bırakılmış modül bırakma.
