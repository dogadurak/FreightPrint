"""Faz 3 validation analysis, written as a paired script so the notebook carries no data.

Run `python notebooks/build_notebook.py` to regenerate `validation_analysis.ipynb` from
this file. The notebook is committed without outputs: executing it locally reproduces the
figures, but the repository never carries customer routes or city names.
"""

# %% [markdown]
# # FreightPrint — Faz 3 doğrulama analizi
#
# Bu defter, sistemin ürettiği sayıları gerçek bir lojistik firmasının iki müşteri için
# hazırladığı karbon raporlarıyla karşılaştırır. Veri seti (`dogrulama_veriseti.csv`)
# gerçek müşteri bilgisi içerdiği için depoya dâhil değildir; bu defter onsuz çalışmaz.
#
# Ölçülen üç şey:
# 1. **Emisyon tutarlılığı** — aynı mesafe ve aynı faktörle hesap mantığımız raporu
#    yeniden üretiyor mu? (hedef: fark < %1)
# 2. **Mesafe tutarlılığı** — kendi hesapladığımız mesafeler referanstan ne kadar sapıyor?
# 3. **Farkın kaynağı** — sapma mesafeden mi, doluluk varsayımından mı, faktörden mi?

# %%
import sys
from pathlib import Path
from statistics import mean, median

import matplotlib.pyplot as plt

sys.path.insert(0, str(Path.cwd().parent / "backend"))

from app.core.geocode import geocode_all
from app.core.network import DATA_DIR, load_terminals
from app.core.road import road_route
from app.core.sea import sea_distance
from app.core.emissions import calculate_shipment, find_factor, load_emission_factors
from app.core.route import Leg, RouteAlternative
from app.core.validation import (
    compare_all_road_baseline,
    compare_emissions,
    load_validation_dataset,
    mean_absolute_percentage_error,
)

REFERENCE_ROAD_FACTOR = 0.121
plt.rcParams["figure.figsize"] = (9, 4)
plt.rcParams["axes.grid"] = True
plt.rcParams["grid.alpha"] = 0.3

# %% [markdown]
# ## 1. Veri setinin yapısı

# %%
records = load_validation_dataset()
multimodal = [r for r in records if r.is_multimodal]

print(f"toplam sevkiyat        : {len(records)}")
print(f"çok modlu              : {len(multimodal)}")
print(f"tamamen karayolu       : {len(records) - len(multimodal)}")
print(f"kaynak rapor sayısı    : {len({r.source_report for r in records})}")
print(f"varış ülkesi sayısı    : {len({r.destination_country for r in records})}")

# %% [markdown]
# Satırların üçte biri hiç deniz ya da demiryolu bacağı içermiyor — bunlar tamamen
# karayolu sevkiyatları. Çok modlu doğrulama yalnızca kalan satırlar üzerinden yapılabilir.
#
# Varış ülkeleri pilot koridoru belirgin şekilde aşıyor (Kosova, Karadağ, Sırbistan,
# Danimarka, İspanya). Mevcut 16 terminallik ağ bu noktaların tamamına hizmet edemez;
# bu, terminal listesinin genişletilmesi gerektiğini gösteren somut bir bulgudur.

# %%
destinations = sorted({r.destination_country for r in records})
print("varış ülkeleri:", ", ".join(destinations))

# %% [markdown]
# ## 2. Emisyon tutarlılığı — hesap mantığı doğru mu?
#
# Bu adımda **rotalama devre dışı**: raporun kendi mesafe sütunlarını ve kendi
# faktörlerini alıp yalnızca hesabı yeniden yapıyoruz. Buradaki fark, hesap mantığındaki
# farktır — mesafeden ya da rota seçiminden gelmez.

# %%
baseline = compare_all_road_baseline(records)
print(f"tam karayolu satırı        : {len(baseline)}")
print(f"%1 içinde eşleşen          : {sum(c.matches for c in baseline)}")
print(f"ortalama mutlak yüzde hata : %{mean_absolute_percentage_error(baseline) * 100:.6f}")

# %% [markdown]
# Tam karayolu senaryosunda **34 satırın 34'ü** birebir tutuyor. Bu, temel formülün
# (`mesafe × ton × faktör`) ve karayolu faktörünün (0,121) doğru olduğunu kanıtlar.

# %%
comparisons = compare_emissions(records)
matching = [c for c in comparisons if c.matches]
outliers = [c for c in comparisons if not c.matches]

print(f"çok modlu satır            : {len(comparisons)}")
print(f"%1 içinde eşleşen          : {len(matching)}")
print(f"eşleşmeyen                 : {len(outliers)}")
print(f"eşleşenlerde ortalama hata : %{mean_absolute_percentage_error(matching) * 100:.9f}")

# %%
fig, ax = plt.subplots()
errors = [c.relative_difference * 100 for c in comparisons]
colours = ["tab:green" if c.matches else "tab:red" for c in comparisons]
ax.bar(range(len(errors)), errors, color=colours)
ax.axhline(1, color="black", linestyle="--", linewidth=0.8)
ax.axhline(-1, color="black", linestyle="--", linewidth=0.8)
ax.set_title("Çok modlu satırlarda emisyon sapması (aynı mesafe, aynı faktör)")
ax.set_xlabel("sevkiyat")
ax.set_ylabel("sapma (%)")
plt.tight_layout()
plt.show()

# %% [markdown]
# Eşleşen 19 satırda hata **sıfıra eşit** — yuvarlama düzeyinde bile fark yok. Hesap
# mantığı doğrulanmıştır. Kalan 3 satır aşağıda ayrıştırılıyor.

# %% [markdown]
# ## 3. Farkın kaynağı — eşleşmeyen 3 satır
#
# Deniz ve demiryolu bacaklarını sabit tutup, raporun bildirdiği CO2'yi üretmek için
# gereken karayolu mesafesini geri hesaplıyoruz.

# %%
print(f"{'rota':<30}{'bildirilen km':>14}{'ima edilen km':>15}{'fark %':>9}")
print("-" * 68)
for c in outliers:
    implied = c.implied_road_km(REFERENCE_ROAD_FACTOR)
    print(
        f"{c.record.origin_city[:13]:<14}->{c.record.destination_city[:13]:<15}"
        f"{c.record.road_km:>14.0f}{implied:>15.0f}{c.relative_difference * 100:>8.1f}%"
    )

# %% [markdown]
# Üç satırda da sapma **tamamen karayolu mesafesinde** çözülüyor: raporun bildirdiği CO2,
# yine raporun kendi karayolu km sütunundan daha kısa bir mesafe ima ediyor. Deniz ve
# demiryolu bacakları birebir tutuyor.
#
# Yani kaynak rapor bu üç satırın emisyonunu, listelediğinden farklı bir mesafe setiyle
# hesaplamış. **Bu bizim hesabımızın hatası değil, kaynak veri içi tutarsızlığıdır.**
# Aynı desen daha önce servis rotası sütununda da görülmüştü: aynı `PENDIK - TRIESTE -
# DUISBURG` rotası bazı satırlarda 990, bazılarında 1190 km demiryolu gösteriyor.

# %% [markdown]
# ## 4. Mesafe tutarlılığı — kendi hesapladığımız karayolu mesafeleri
#
# Kalkış ve varış noktaları Nominatim ile koordinata çevrilip OSRM ile rotalanıyor,
# sonuç raporun `tam_karayolu_km` sütunuyla karşılaştırılıyor.

# %%
places = sorted({r.origin for r in records} | {r.destination for r in records})
coords = geocode_all(places)
resolved = sum(1 for c in coords.values() if c)
print(f"coğrafi kodlanan konum: {resolved}/{len(places)}")

# %%
road_rows = []
for record in records:
    origin, destination = coords.get(record.origin), coords.get(record.destination)
    if not origin or not destination or not record.all_road_km:
        continue
    computed = road_route(origin, destination)
    deviation = (computed.distance_km - record.all_road_km) / record.all_road_km * 100
    road_rows.append((record, computed, deviation))

deviations = [d for _, _, d in road_rows]
print(f"karşılaştırılan satır      : {len(road_rows)}")
print(f"ortalama mutlak yüzde hata : %{mean(abs(d) for d in deviations):.1f}")
print(f"medyan sapma               : %{median(deviations):+.1f}")
print(f"%10 içinde                 : {sum(abs(d) < 10 for d in deviations)}/{len(deviations)}")
print(f"feribot içeren rota        : {sum(1 for _, c, _ in road_rows if c.ferry_km > 0)}")

# %%
fig, ax = plt.subplots()
ax.hist(deviations, bins=15, color="tab:blue", edgecolor="white")
ax.axvline(0, color="black", linewidth=1)
ax.set_title("Kendi karayolu mesafemizin referanstan sapması")
ax.set_xlabel("sapma (%)")
ax.set_ylabel("sevkiyat sayısı")
plt.tight_layout()
plt.show()

# %% [markdown]
# Sapma dar bir bantta ve sıfır etrafında toplanıyor. Kalan fark iki bilinen sebepten
# geliyor: şehir merkezine coğrafi kodlama (rapor tesis adresini kullanmış olabilir) ve
# rota tercihi farkları (ücretli yol, kamyon kısıtları).
#
# **Bu metriğin iki sınırı var:**
#
# 1. Dört satır karşılaştırmaya girmiyor, çünkü varış posta kodu kaynak veride eksik
#    ya da hatalı yazılmış: biri ülkesinin gerektirdiği hane sayısından kısa, biri
#    zorunlu harf ekini taşımıyor, biri de bir CEDEX (kurumsal dağıtım) kodu.
#    Bunlar yanlış bir konuma zorlanmak yerine çözümsüz bırakıldı — hatalı bir
#    koordinat, sessizce MAPE'yi bozardı.
# 2. Bir varış noktasının adı ülkesinde birden fazla yerleşime karşılık geliyor.
#    Nominatim'in seçtiği aday −%3,3 sapma verirken, otoyol üzerindeki lojistik
#    merkezi olan diğer aday −%10,3 verir ve "hepsi %10 içinde" ifadesini kırar.
#    Hangisinin kastedildiği kaynak veriden anlaşılmıyor.

# %% [markdown]
# ## 5. Deniz mesafeleri — searoute'un Korint Kanalı sorunu
#
# Faz 0'da searoute'un gerçek gemilerin geçemeyeceği Korint Kanalı üzerinden kısayol
# çizdiği bulunmuştu. Bunun ölçülebilir etkisi:

# %%
terminals = load_terminals()
import csv

with open(DATA_DIR / "service_legs.csv", encoding="utf-8") as f:
    sea_legs = [row for row in csv.DictReader(f) if row["mode"] == "sea"]

clean, crossing = [], []
print(f"{'bacak':<26}{'referans':>10}{'searoute':>10}{'sapma':>9}  Korint")
print("-" * 64)
for leg in sea_legs:
    reference = float(leg["ref_distance_km"])
    result = sea_distance(
        terminals[leg["from_terminal"]].coords, terminals[leg["to_terminal"]].coords
    )
    deviation = (result.distance_km - reference) / reference * 100
    name = f"{terminals[leg['from_terminal']].name}-{terminals[leg['to_terminal']].name}"
    flag = "EVET" if result.crosses_corinth_canal else "-"
    print(f"{name:<26}{reference:>10.0f}{result.distance_km:>10.0f}{deviation:>8.1f}%  {flag}")
    (crossing if result.crosses_corinth_canal else clean).append(abs(deviation))

print("-" * 64)
print(f"Korint geçmeyen bacaklarda MAPE : %{mean(clean):.1f}  (n={len(clean)})")
print(f"Korint geçen bacaklarda MAPE    : %{mean(crossing):.1f}  (n={len(crossing)})")

# %% [markdown]
# Kanaldan geçmeyen tek bacak **%4,2**, geçenler **%21,9** sapıyor.
#
# **Bu sonuç dikkatle okunmalı.** Kontrol grubu tek bir bacaktan (n=1) ibaret ve o bacak
# (Trieste–Patras) ağdaki tek Adriyatik-içi bağlantı. Yani "Korint'ten geçiyor" ile
# "Marmara/Doğu Akdeniz çıkışlı" değişkenleri tamamen çakışık — bu veriyle ikisi
# birbirinden ayrılamaz. Ayrıca %21,9'un büyük kısmını tek bir uç değer taşıyor
# (Pendik–Patras, −%42,8).
#
# Dolayısıyla bu tablo Korint kısayolunun sorunlu olduğuna **işaret eder**, ama tek
# başına kanıtlamaz. Kanıt Faz 0'daki doğrudan gözlemdir: rota koordinatları kanalın
# üzerinden geçiyor ve searoute'un `restrictions` parametresi bu geçişi engellemiyor.
#
# Sistem bu yüzden servis bacaklarında referans mesafeyi esas alır; searoute değeri
# yalnızca karşılaştırma için hesaplanır ve kanaldan geçen rotalar "kullanılamaz"
# olarak işaretlenir.

# %% [markdown]
# ## 6. Faktör kapsamı — raporun deniz faktörü hangi moda ait?
#
# Buraya kadar her şey raporun **kendi** faktörleriyle hesaplandı; amaç hesap mantığını
# doğrulamaktı. Şimdi faktörlerin kendisine bakalım.
#
# Rapor deniz için 0,012 kg CO2/ton-km kullanmış. Bu bir konteyner gemisi büyüklüğünde bir
# değer — ama raporun listelediği servisler ro-ro. Ro-ro gemisi treyler taşır: yükün yanında
# treylerin darasını da taşır, doluluk oranı düşüktür ve daha hızlı seyreder.

# %%
factors = load_emission_factors()
print(f"{'mod':<10}{'rapor':>10}{'GLEC':>10}{'oran':>9}")
print("-" * 39)
for mode in ("road", "sea", "rail"):
    reported = find_factor(factors, mode, factor_set="reference").value
    glec = find_factor(factors, mode, factor_set="glec").value
    print(f"{mode:<10}{reported:>10.3f}{glec:>10.3f}{glec / reported:>8.1f}x")

# %% [markdown]
# İki sapma da **aynı yöne** çalışıyor: deniz olduğundan temiz, karayolu olduğundan kirli
# görünüyor. İkisi birlikte çok modlu seçeneği belirgin biçimde kayırır.
#
# Aynı sevkiyatın iki faktör setiyle sonucu:

# %%
reported_route = RouteAlternative(
    legs=[
        Leg("road", "origin", "port", 20),
        Leg("sea", "port", "hub", 2500),
        Leg("rail", "hub", "terminal", 950),
        Leg("road", "terminal", "destination", 41),
    ],
    label="multimodal",
)
all_road_route = RouteAlternative(
    legs=[Leg("road", "origin", "destination", 2515)], label="all-road"
)

print(f"{'faktör seti':<22}{'çok modlu':>12}{'tam karayolu':>14}{'tasarruf':>11}{'oran':>8}")
print("-" * 67)
for factor_set, scope in (("reference", "TTW"), ("glec", "TTW"), ("glec", "WTW")):
    baseline, alternative = calculate_shipment(
        [all_road_route, reported_route], tonnage=24, factor_set=factor_set, scope=scope
    )
    saving = alternative.saving_co2_kg
    print(
        f"{factor_set + ' / ' + scope:<22}{alternative.total_co2_kg:>12,.0f}"
        f"{baseline.total_co2_kg:>14,.0f}{saving:>11,.0f}{saving / baseline.total_co2_kg:>7.0%}"
    )

# %% [markdown]
# **Bu koridorda "çok modlu taşıma karbon kazandırır" iddiası GLEC faktörleriyle
# savunulamıyor.** Demiryolu bacağı hâlâ net kazanç (0,020'ye karşı 0,060, üç kat temiz);
# sorun ro-ro deniz bacağında.
#
# Bu bir denetim iddiası değildir — hangi ro-ro esasının doğru olduğu bir muhasebe
# tercihidir ve GLEC üç farklı satır verir (42, 63, 93 g/ton-km). Sistem en özgün eşleşmeyi
# (sadece treyler, 63) varsayılan alır ve hangi setle hesap yaptığını her çıktıda yazar.

# %% [markdown]
# ## 7. Sonuç
#
# | Ölçüt | Hedef | Sonuç |
# |---|---|---|
# | Emisyon tutarlılığı (tam karayolu) | fark < %1 | **34/34 satır**, hata ≈ 0 |
# | Emisyon tutarlılığı (çok modlu) | fark < %1 | **19/22 satır**, eşleşenlerde hata = 0 |
# | Karayolu mesafe sapması | raporlanabilir | MAPE **%1,9**; 30/30 satır %10 içinde |
# | Deniz mesafe sapması | raporlanabilir | temiz bacak %4,2 — Korint geçen %21,9 (n=1'e karşı n=5) |
# | Faktör kapsamı | — | raporun deniz faktörü ro-ro değil, konteyner gemisi düzeyinde |
#
# **Bölüm 9.3'ün emisyon eşiği (fark < %1) 22 satırın 19'unda karşılanmıştır.**
# Kalan 3 satır, kendi bildirdiği km sütunlarının ürettiğinden daha düşük bir CO2
# raporluyor. Bu farkın karayolu bacağından geldiği yorumu tutarlıdır ama yalnız bu
# veriyle kanıtlanamaz; kesin olan, kaynak raporun kendi içinde tutarsız olduğudur.
#
# **Bu analizin kabul edilmiş sınırları:**
# - Karayolu metriği 34 satırın 30'unu kapsıyor; 4'ü kaynak verideki hatalı posta
#   kodları yüzünden coğrafi kodlanamadı.
# - Korint karşılaştırmasının kontrol grubu tek bacak; sonuç işaret eder, kanıtlamaz.
# - Bir varış adı çok anlamlı; alternatif okuma %10 eşiğini kırar.
#
# **Kapatılması gereken açıklar:**
# - Deniz mesafesi için searoute'a doğrudan güvenilemez; kanal kısıtlı bir ağ ya da
#   kalibrasyon gerekiyor.
# - Demiryolu mesafesi hiç hesaplanmıyor; TEN-T/OpenRailwayMap entegrasyonu yok.
# - Terminal ağı, veri setindeki varış noktalarının bir kısmını kapsamıyor.
# - `data/service_legs.csv` Pendik–Bari için 1755 km diyor, doğrulama veri seti aynı
#   bacak için 1825 km. İki referans birbiriyle çelişiyor, hangisinin doğru olduğu
#   belirlenmedi.
