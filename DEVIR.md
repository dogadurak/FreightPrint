# Devir — nerede kaldık, sırada ne var

**Son güncelleme: 27 Ağustos 2026.**

Bu dosya kapsamı tanımlamaz — onun yeri [`PROJE_FreightPrint.md`](PROJE_FreightPrint.md).
Burada yalnızca **açık işler ve neden açık oldukları** var. Bir iş bittiğinde buradan
silinir; bitmemiş bir işi burada bırakmak, hatırlamaya çalışmaktan iyidir.

---

## Durum

Faz 0–8 tamamlandı. **649 test** geçiyor, CI'da dört denetim koşuyor
(`check_tracked_files.py`, `check_data_fields.py`, `source_terminals.py`,
`check_turkish_text.py`).

Pano ekran görüntüleri README'de; `python scripts/shoot_dashboard.py` ile yenilenir.
**Yenilemeden önce sunucuyu yeniden başlat** — görüntüler bir kez, düzeltmelerden önce
başlatılmış bir sunucudan alındığı için kaynak koda aykırı çıktı.

**Altı dış kaynak bağlı** — hepsi `data/external/` altında, betikle türetiliyor,
`--check` ile doğrulanıyor. Bulguları Bölüm 8.3'te ve `data/external/README.md`'de;
**ezberden tekrarlama, oradan oku.**

| Kaynak | Bulgu |
|---|---|
| Eurostat `road_go_ta_vm` | Boş dönüş ~%12, GLEC %30 varsayıyor |
| EU MRV / THETIS-MRV | GLEC ro-ro faktörü filonun orta yarısının içinde |
| NGA Pub. 151 | Deniz mesafeleri: tablo 6/6 bacakta %9–26 **yüksek** |
| ERA RINF | Ülke içinde %1,0 doğru; Avusturya %23 bütünlükte, koridor rotalanamıyor |
| OSM / OpenRailRouting | Demiryolu: tablo 7/7 bacakta %4–33 **düşük** |
| OSM / Overpass | Terminal konumları: limanlar <1 km, yük terminalleri 3,5–4,0 km sapıyor |

---

## Sırada — bu sırayla

### 1. "Hangi sayı doğru?" sorusunu kapat ← **buradan başla**

Deniz mesafeleri **%9–26 yüksek**, demiryolu **%4–33 düşük** okuyor — düzeltmeler **ters
yönde**. Motor şu an ikisini de uygulamıyor, ikisini de panoda yanında gösteriyor.

Okuyanın soracağı ilk soru bu ve yazılı bir cevabı yok. Yapılacak olan **gerekçeli bir
tavsiye yazmak**, sessizce değiştirmek değil:

- İkisi birden uygulanırsa çok modlu kazanç %19,4'ten %10,4'e iner (yalnız deniz
  uygulanırsa %7,3 — yani ters yönler bulguyu *sağlamlaştırıyor*).
- Karar hangi koşulda hangi sayının kullanılacağını söylemeli, ve neden motorun
  varsayılanının değişmediğini.

> **Kural değişmiyor:** dış gözlem varsayımın yerine geçmez, yanına konur. Sessizce
> değiştirmek motoru, doğrulandığı müşteri raporunu yeniden üretemez hâle getirir.

### 2. Türk terminallerine kaynak — süreli kutu (~2 saat)

Pendik, Yalova, Ambarlı, Halkalı: **dördü de kaynaksız**, ve dördü de Türk. Aynı boşluk
üç kaynakta birden çıktı (Eurostat bildirimi yok, RINF bildirimi yok, Pub 151'de yalnızca
İstanbul/Derince/Mersin var). UN/LOCODE dördüncü adaydı: **unece.org otomatik isteklere
403 dönüyor**, topluluk aynaları birbiriyle tutmuyor (2,0 MB / 7,3 MB).

Kalan adaylar: TCDD'nin kendi yayınları, pinlenebilir bir UN/LOCODE aynası.

> **Sonuç çıkmaması da geçerli bir sonuç.** Çıkmazsa "arandı, bulunamadı" diye yazılır —
> ama *nasıl arandığı* kaydedilerek. Bu tam olarak Köln/Duisburg'da yanlış yapılan şeydi:
> yanlış yerde arayıp "yok" diye yazmak, aramamakla aynı çıktıyı veriyor.

---

## Değişmeyen kısıtlar

- **Push her seferinde ayrıca istenir.** Commit'lemek serbest, push için açık onay şart.
- **Git geçmişindeki müşteri verisi:** kullanıcının kendi işi. `filter-repo` çalıştırma,
  force-push yapma, depo görünürlüğünü değiştirme.
- **Müşteri verisi asla commit'lenmez:** `dogrulama_veriseti.csv`, `data/geocode_cache.json`,
  `data/route_cache.sqlite`, `osrm-data/`, `data/external/*.xlsx`, iki `CARBON FOOTPRINT
  REPORT ... .xlsx`. `check_tracked_files.py` her commit'te bunu zorluyor.
- **Kapsam:** denetim aracı değil, canlı gemi takibi değil, "dijital ikiz" değil, TMS
  değil. Arayüz "sertifika", "denetim", "onaylı" demez.
