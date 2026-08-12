# AutoFlow — Genişleme Araştırması ve Yol Haritası

**Tarih:** 2026-08-03 · **Temel alınan sürüm:** `develop` @ 9c7d28b (285 test yeşil)
**Kapsam:** motorun bugünkü tavanları, sektör karşılaştırması, eklenmesi gereken
trigger/koşul/aksiyon envanteri, ve AI destekli ikinci aşamanın mimarisi.

> Bu belge bir **plan**dır, bir değişiklik kaydı değil. Uygulanan her madde
> `logbook.md`'ye zaman damgasıyla ayrıca yazılacak. Mimari sözleşmeler
> `CLAUDE.md`'de kalır; burası "sırada ne var ve neden o sırada" sorusunun cevabı.

---

## 0. Yönetici Özeti

**Tez:** AutoFlow'un asıl eksiği entegrasyon sayısı değil, **motorun ifade gücü**.
Bugün bir otomasyon şunu diyebiliyor: *"bu olay olursa, şu koşullar sağlanırsa,
şu 3 bildirimi sırayla gönder."* Diyemediği şey: *"şunu kontrol et, sonucuna göre
ikiye ayrıl, 24 saat bekle, cevap gelmediyse ikinci kanaldan dene."* Bu ikinci cümle
sektörde **taban özellik** — ve bizim müşteri profilimizin (çok şubeli işletme)
en çok istediği kalıp da tam olarak o.

**En kritik bulgu — AI fazı için:** AI'ın otomasyon kurabilmesi, motorun ifade
edebildiği kadarıyla sınırlı. Bugünkü motora tool calling bağlarsak, AI'a
"doğrusal bildirim listesi" yazdırmış oluruz; bu ne etkileyicidir ne de kullanışlı.
Aynı şekilde `ai_eval`'in maliyeti kontrol edilemez, çünkü koşul motorunda **kısa
devre yok** — LLM'li bir koşul, önündeki ucuz koşul zaten `False` dönmüşken de
çağrılır ve parayı yakar. **AI fazı, Faz 1 ve Faz 2'nin üstünde durur.** Sırayı
bozmak, AI'ı ifade edemeyeceği bir motora bağlamak olur.

**Stratejik konum:** Zapier'ın 8.000 konnektörüyle yarışmayacağız — o yarış
kaybedilmiş bir yarış. AutoFlow'un rakiplerde **olmayan** iki varlığı var:
1. Kiracı→şube hiyerarşisi ve rol tabanlı erişim **çekirdekte**. "Zincir merkezi
   kurar, şube sınırlı özelleştirir" modeli Zapier/Make/n8n'de ifade edilemiyor.
2. Türkiye'ye özgü uyum yüzeyi (İYS, KVKK, yerel SMS operatörleri). İYS entegrasyonu
   bir "özellik" değil, ticari ileti göndermek için **yasal giriş bileti**; küresel
   rakiplerin hiçbirinde yok.

Genişlik ihtiyacını konnektör yazarak değil, **MCP client** desteğiyle kapatacağız
(§6.6) — 2026'da entegrasyon genişliğinin en yüksek kaldıraçlı yolu bu.

**Faz özeti:**

| Faz | Konu | Süre | Neden bu sırada |
|---|---|---|---|
| 0 | Doğrulama + 4 canlı defekt | 1–2 hf | Zamanlayıcı bugünkü hâliyle yanlış saatte ateşliyor |
| 1 | Motor derinliği (dallanma, adım çıktısı, bekleme) | 4–6 hf | Sonraki her şeyin veri modeli buradan çıkıyor |
| 2 | Dayanıklılık + gözlemlenebilirlik | 4–6 hf | Replay/dry-run olmadan AI'a simülasyon tool'u verilemez |
| 3 | Entegrasyon genişliği + uyum | 4–8 hf | Gelir kapısı: İYS/WhatsApp olmadan üretimde satılamaz |
| 4 | AI (üç yüzey) | 6–10 hf | Faz 1+2'nin üstünde durur |
| 5 | Ölçek / kurumsal | sürekli | Talep geldikçe |

---

## 1. Bugün Neredeyiz — Yetenek Envanteri

### 1.1 Var olan

| Katman | Bugünkü hâl |
|---|---|
| Trigger | `scheduled`, `manual`, `webhook`, `signal` (Django model sinyalleri) |
| Koşul | 6 operatör (`eq/neq/gt/lt/contains/in`), düz liste, soldan sağa AND/OR |
| Aksiyon | 5 tip (`email/sms/whatsapp/instagram/webhook`), `order`'a göre doğrusal |
| Hata | Aksiyon başına `stop` / `continue` / `retry` (3 deneme, backoff yok) |
| Kanal | `Channel` modeli + provider registry; credential `JSONField` |
| Şablon | `{{alan}}` + `{%if alan%}…{%endif%}`, alanlar `Template.fields`'ta beyan edilir |
| Log | Çalıştırma başına tek `AutomationLog` satırı |
| Yetki | İki katman: DRF permission sınıfları + `RoleBasedAccessMixin` |
| Cache | Redis + Lua, index-set tabanlı read-through |

Mimarinin **doğru** kurulmuş yanı, ve genişleme planının üzerine oturduğu temel:
şema güdümlü konfigürasyon. Yeni bir kanal eklemek model değişikliği gerektirmiyor
(`CLAUDE.md` §3.7). Bu karar, §6.2'de göreceğimiz gibi AI fazının da kilit taşı —
çünkü LLM'e verilecek tool şeması, kullanıcıya gösterilecek form ve API
dokümantasyonu **aynı kaynaktan** üretilebilir.

### 1.2 Yapısal tavanlar

Bunlar bug değil; tasarımın bugün ulaştığı sınırlar. Her biri kodda doğrulandı.

**T1 — Dallanma yok.** `AutomationExecutor.execute_actions()` bir `for` döngüsü;
`AutomationAction.order` doğrusal bir sıra. "VIP müşteriye WhatsApp, diğerine SMS"
tek otomasyonda ifade edilemiyor; iki ayrı otomasyon ve koşul kopyalaması gerekiyor.
Zapier'da bu **Paths**, n8n'de **If/Switch/Merge** — ikisinde de taban özellik.

**T2 — Koşullarda öncelik yok.** `evaluate_conditions()` soldan sağa katlıyor:
`A AND B OR C` daima `((A AND B) OR C)`. Kullanıcı `A AND (B OR C)` yazamıyor.
Ayrıca **kısa devre yok** — `A` `False` olsa bile `B` ve `C` de değerlendiriliyor.
Bugün bu sadece israf; `ai_eval` geldiğinde doğrudan **fatura**.

**T3 — Adımlar arası veri akmıyor.** `BaseAction.prepare()` yalnızca
`action.config` + tetikleyici payload'ını birleştiriyor. Aksiyon 1'in çıktısı
(oluşan kaydın id'si, HTTP cevabı, AI'ın ürettiği metin) Aksiyon 2'ye **görünmüyor**.
Yani bugünkü "zincir" aslında zincir değil, aynı girdiden beslenen paralel
bildirimler listesi. Bu tavan kalkmadan `ai_classify` gibi bir aksiyonun hiçbir
anlamı olmaz: sınıflandırır ama sonucu kimse okuyamaz.

**T4 — Çalıştırma durumu yok, replay imkânsız.** `AutomationLog` çalıştırma başına
tek satır: hangi adımda kalındığı, adımların girdi/çıktısı, süresi, deneme sayısı
kayıtlı değil. Worker çökerse yarım kalan çalıştırma tespit bile edilemez.
`_retry()` senkron çalışıyor — worker thread'ini meşgul ediyor, backoff yok,
ölü mektup kuyruğu yok.

**T5 — Idempotency yok.** `WebhookAutomationView` her POST'ta koşulsuz
`run_automation.delay()` çağırıyor. Kaynak sistem retry ederse otomasyon iki kez
çalışır: iki SMS, iki fatura. Zapier bile instant trigger'da dedup yapmadığını açıkça
söyleyip sorumluluğu kaynağa atıyor — ama biz *alıcı* taraftayız; bizde bu bir
mazeret değil.

**T6 — Olay-göreli bekleme yok.** "Randevudan 24 saat önce hatırlat", "siparişten
3 gün sonra yorum iste" — çok şubeli hizmet işletmesinin **en yaygın iki kalıbı** —
bugün ifade edilemiyor. Sadece mutlak zamanlı cron var.

**T7 — Kimlik bilgileri düz metin.** `Channel.credentials = JSONField()`.
Bir veritabanı dökümü = tüm kiracıların API anahtarları. OAuth desteği yok; sadece
statik anahtar. Instagram ve WhatsApp'ın gerçek entegrasyonu OAuth + token yenileme
istiyor, yani bu tavan aynı zamanda bir **entegrasyon tavanı**.

**T8 — Çalışma zamanı kiracı izolasyonu yok.** Tek Celery kuyruğu. Bir kiracının
10.000 kayıtlık toplu işi, diğer tüm kiracıların randevu hatırlatmasını geciktirir.
Eşzamanlılık sınırı, kota, öncelik yok.

**T9 — Şablon motoru zayıf.** Nokta yolu yok (`{{customer.name}}` çalışmaz), filtre
yok (tarih/para formatı), döngü yok. `Template.fields` her alanı elle beyan etmeyi
zorunlu kılıyor — 20 alanlı bir sipariş özeti için 20 satır JSON.

**T10 — Versiyonlama yok.** `AutomationWriteSerializer.update()` trigger, condition
ve action'ları **siliyor ve yeniden yaratıyor**. Düzenleme geçmişi yok, geri alma
yok; devam eden çalıştırmalar tanımsız duruma düşüyor. `Template.version` alanı var
ama hiç kullanılmıyor.

**T11 — Dry-run yok.** Bir otomasyonu denemenin tek yolu gerçekten SMS göndermek.

**T12 — Log payload'ı sınırsız büyüyor.** `AutomationReadSerializer` **tüm**
`logs`'u iç içe döndürüyor, sayfalama yok — ve bu cevap Redis'te cache'leniyor.
Dakikada bir çalışan bir otomasyonun detay cevabı zamanla megabaytlara çıkar.
T4'ün çözümüyle birlikte ele alınmalı.

---

## 2. Sektör Manzarası

### 2.1 Üç büyük oyuncu

| | Zapier | Make | n8n |
|---|---|---|---|
| Entegrasyon | 8.000+ | 1.500+ | 400+ yerel + HTTP node |
| Dallanma | Paths | Router | If / Switch |
| Filtre | Filter (akışı durdurur) | Filter | Filter |
| Döngü | Looping by Zapier | Iterator/Aggregator | Loop Over Items (Split In Batches) |
| Bekleme | Delay | Sleep | Wait |
| Birleştirme | — | Aggregator | Merge |
| Alt akış | Sub-Zaps | — | Execute Sub-workflow |
| Hata yönetimi | Adım başına error handler (alternatif akış) | Error handler rotaları | Error Trigger workflow |
| Otomatik tekrar | Autoreplay: 5 deneme, ~10s 35dk yayılım | var | var |
| Veri eşleme | Formatter + JSONPath | Pill tabanlı görsel eşleme | JS ifadeleri `{{ }}`, JMESPath, JSONata |
| Barındırma | SaaS | SaaS | Self-host (fair-code) |
| AI | Zapier Agents, Copilot | Maia (doğal dilden senaryo) | AI Agent node (LangChain), 70+ AI node |
| MCP | Resmî MCP server | — | MCP Server Trigger + MCP Client Tool, Nisan 2026'dan beri instance düzeyi MCP server |

**Okuma:** Kontrol akışı primitifleri (dallan / filtrele / bekle / döngü / birleştir /
alt akış) üçünde de var. Bunlar farklılaştırıcı değil, **taban**. AutoFlow'da hiçbiri
yok. §1.2'deki T1, T6 ve T3 doğrudan bu tabloyla ölçülüyor.

### 2.2 Asıl sınıf farkı: dayanıklı yürütme

Zapier/Make/n8n'in üstünde ikinci bir katman var: Temporal, Inngest, Restate,
Trigger.dev. Bunlar iş akışını **çökmeden sağ çıkan** bir yürütme olarak modelliyor:

- **Adım memoizasyonu** (Inngest): her adım bir kez çalışır, sonucu kalıcı yazılır;
  yeniden yürütmede tamamlanmış adımlar atlanıp saklanan sonuçları enjekte edilir.
- **Deterministik replay** (Temporal): iş akışı fonksiyonu baştan yürütülür, olay
  geçmişi tamamlanmış işi atlatır.
- **Journal replay** (Restate): tamamlanan işlemler günlüğe yazılır; kurtarmada
  günlük tekrar oynatılır.
- Üçünde de **yerleşik** olarak: eşzamanlılık kontrolü, throttling, debounce,
  rate limit, idempotency anahtarları, dayanıklı zamanlayıcılar.

Bunun AI ile kesişimi kritik: **LLM çağrısını yeniden yürütmek, token'ı yeniden
ödemek demek.** Adım memoizasyonu olmadan bir worker çökmesi, o çalıştırmadaki her
AI kararını yeniden faturalandırır. §7 Faz 2'nin Faz 4'ten önce gelmesinin
teknik gerekçesi budur.

AutoFlow'un Temporal olmasına gerek yok. Ama T4'ün çözümü, bu ailenin en ucuz
fikrini ödünç almalı: **adım başına kalıcı durum + memoizasyon.**

### 2.3 AI katmanı — 2026'da ne standartlaştı

Araştırmanın topladığı ortak nokta üç madde:

1. **MCP evrensel arayüz oldu.** Kurumsal iPaaS seçiminde MCP desteği ayırt edici
   hâle geldi. Platformlar iki yönü de destekliyor: dış MCP sunucularını *tool
   olarak tüketmek* ve kendi iş akışlarını *MCP server olarak sunmak*.
2. **Üretime hazırlık dengeli.** Yapılandırılmış görevlerde (çıkarım, sınıflandırma,
   özetleme) AI üretime hazır; **çok adımlı otonom karar vermede hâlâ deneysel.**
   Bu, AutoFlow'un AI fazını sıralarken uyacağı ölçü: önce sınıflandırma ve içerik
   üretimi, en son araçlı ajan.
3. **İnsan onayı geri alınamaz eylemlerde zorunlu.** Yerleşen kural: onay, eylem
   *geri alınamaz, maliyetli, regüle veya geniş etki alanlıysa* istenir; her eyleme
   onay koymak "güvenlik faydası üretmeden sürtünme yaratır". n8n bunu tool düzeyinde
   uyguluyor: hassas tool çağrılarından önce insan onayı istenebiliyor.

**LLM-as-judge** literatürünün üretim deseni (bu doğrudan `ai_eval`'i bağlar):
katmanlı yığın — önce ucuz deterministik kontroller, karar veremediğinde LLM'e
çıkılır. Bilinen zaaflar: çalıştırmalar arası tutarsızlık, sistematik skor
yanlılıkları, zayıf alan kalibrasyonu. Yapılandırılmış çıktı (serbest üretim yerine
tanıma) davranışı belirgin biçimde stabilize ediyor.

### 2.4 Nerede yarışmamalıyız

- **Konnektör sayısı.** 8.000'e karşı 400 bile yetişemedi; n8n bunu HTTP node ve MCP
  ile kapatıyor. Biz de öyle yapacağız (§6.6).
- **Görsel canvas.** Frontend işi. Backend'in yapması gereken, canvas'ı *mümkün
  kılmak*: düğüm koordinatlarını `metadata`'da taşımak ve grafiği API'den
  ifade edilebilir kılmak. Faz 1'in veri modeli bunu gözetmeli.
- **Kullanıcı kodu çalıştırma** (Zapier Code, n8n Code node). Sandbox'ın güvenlik
  maliyeti, çok kiracılı bir sistemde bu aşamada kaldıramayacağımız kadar yüksek.
  İhtiyaç doğarsa MCP client ile dışarıda çalıştırılır.

---

## 3. Sektör Talepleri

### 3.1 Dikey: çok şubeli işletme

AutoFlow'un doğal pazarı bu ve veri modeli zaten buna göre kurulmuş. Araştırmadan
çıkan, ölçülmüş etkili kalıplar:

| Kalıp | Neden isteniyor | Bugün ifade edilebilir mi |
|---|---|---|
| Randevu hatırlatma (T–24s / T–2s) | Katılan lokasyonlarda no-show ~%20 azalıyor | ❌ T6 |
| Yorum/değerlendirme isteme (ziyaretten N gün sonra) | Yerel SEO ve itibar | ❌ T6 |
| Yeniden rezervasyon / geri kazanım (60 gündür gelmeyen) | Uyuyan üyelerin ~%10'u geri dönüyor | ❌ T6 + T1 |
| Üyelik / paket / hediye kartı bildirimleri | Sözleşme yenileme geliri | ⚠️ kısmen |
| Doğum günü / yıldönümü kampanyası | Klasik, yüksek dönüşüm | ❌ T6 |
| Sepet terk / sipariş takibi | E-ticaret ayağı | ⚠️ webhook ile kısmen |
| Vardiya / personel hatırlatması | İç operasyon | ✅ |
| Stok eşiği uyarısı | İç operasyon | ⚠️ signal + koşul, ama sayısal karşılaştırma zayıf |

Kritik yapısal talep: **"merkezde tanımla, şubede yerel esneklik tanı."** Zincir
merkezi kampanyayı kurar; şube müdürü metni yerelleştirir ama zamanlamayı veya
hedef kitleyi değiştiremez. AutoFlow'un tenant/branch + `allowed_roles` modeli buna
**en yakın duran** altyapı — ama bugün "devral ve kısmen özelleştir" kavramı yok.
Bu, rakiplerin ifade *edemediği* şey ve en savunulabilir farkımız (§7 Faz 5).

### 3.2 Türkiye'ye özgü uyum — özellik değil, giriş bileti

**İYS (İleti Yönetim Sistemi).** 6563 sayılı Elektronik Ticaretin Düzenlenmesi
Hakkında Kanun kapsamında, ticari elektronik ileti (arama, SMS, e-posta) gönderen
tüm hizmet sağlayıcıların İYS'ye kayıt olması ve alıcı onaylarını sisteme yüklemesi
zorunlu. Kayıtsız veya onaysız gönderimde **aykırılık başına 15.000 TL'ye kadar
ceza**.

Bunun AutoFlow için anlamı net: **SMS ve e-posta aksiyonları, gönderimden önce İYS
izin durumunu kontrol etmek zorunda.** Bu bir provider değil, provider'ların
önündeki bir **ön kontrol kapısı** (`ComplianceGate`). İzin yoksa adım
`skipped_compliance` ile sonlanmalı ve bu **loglanmalı** — denetimde ispat gerekir.

Bu maddeyi öne çıkarıyorum çünkü: (a) yasal risk taşıyor, (b) küresel rakiplerin
hiçbirinde yok, (c) uygulanması görece ucuz, (d) satışta doğrudan bir cümle:
*"AutoFlow ile gönderdiğiniz her ticari ileti İYS onayından geçer."*

**WhatsApp Business Platform.** Bugünkü `WhatsAppProvider` mecranın kurallarından
habersiz. Gerçekte:
- Müşterinin son mesajından itibaren **24 saatlik servis penceresi** var; pencere
  içinde serbest metin gönderilebilir, dışında **yalnızca önceden onaylanmış şablon**.
- Şablonun kategorisi (marketing / utility / authentication) onay anında belirlenir
  ve fiyatı belirler. Pazarlama şablonu, canlı servis penceresi içinde bile pazarlama
  ücretine tabi.
- **1 Ekim 2026'dan itibaren** Meta, bugün ücretsiz olan pencere içi utility ve
  service mesajlarını da ücretlendirmeye başlıyor.

Yani WhatsApp aksiyonu şu üçünü bilmek zorunda: **şablon kaydı, pencere durumu,
kategori.** Aksi hâlde gönderimler ya reddedilir ya da beklenmedik fatura üretir.
Ekim 2026 değişikliği, maliyet görünürlüğünü (§9) bir "güzel olurdu" olmaktan
çıkarıp gereksinim hâline getiriyor.

**KVKK.** Üç somut gereksinim: `AutomationLog.execution_context` kişisel veri
taşıyor → saklama süresi ve maskeleme politikası; silme hakkı → bir müşteri
silindiğinde geçmiş çalıştırma kayıtlarından da temizlenmesi; ve AI fazında
**veri yurt dışına çıkışı** → hangi payload'ın hangi modele gittiğinin kaydı
(§6.3'teki denetim izi bunu da karşılar).

**Yerel operatörler.** Netgsm ve İletiMerkezi BTK lisanslı, İYS entegrasyonlu ve
kontör tabanlı fiyatlamayla Twilio'dan ucuz; teslim edilmeyen SMS ücretlendirilmiyor.
Türkçe destek ve yerel faturalama da müşteri tarafında belirleyici. Provider
kayıt defterine bunlar eklenmeli — mimari zaten buna hazır (yeni provider + yeni
credential şeması, model değişikliği yok).

### 3.3 "Zapier klonu olma" tuzağı

Genel amaçlı bir iPaaS olmaya çalışmak, AutoFlow'un iki avantajını da (dikey
odak, kiracı/şube hiyerarşisi) harcayıp kaybedilmiş bir konnektör yarışına sokar.
Doğru çerçeve: **çok şubeli işletme için müşteri iletişimi ve operasyon
otomasyonu**; genel entegrasyon ihtiyacı HTTP aksiyonu ve MCP client ile karşılanır.

---

## 4. Özellik Araştırması — Ne Eklenmeli

Efor: **S** ≈ günler, **M** ≈ 1–2 hafta, **L** ≈ 3+ hafta.

### 4.1 Trigger'lar

| Yeni trigger | Neden | Bu kod tabanında nasıl | Efor | Faz |
|---|---|---|---|---|
| `polling` | Webhook sunamayan dış sistemler (çoğu POS, muhasebe yazılımı). Zapier'ın trigger'larının çoğu polling. | Yeni `TriggerType`; config `{url, method, headers, items_path, dedup_field, interval}`. Mevcut `PeriodicTask` altyapısı kullanılır. Yeni `PollCursor` modeli son görülen `dedup_field` değerlerini tutar — Zapier'ın "yeni id gördüysen çalıştır" modeli. | M | 3 |
| `delayed` / `relative` | §3.1'deki kalıpların yarısı buna bağlı ("randevudan 24s önce") | `ClockedSchedule` (django-celery-beat) + bir olaya göre hesaplanan `eta`. Faz 1'deki `wait` adımıyla aynı altyapıyı paylaşır. | M | 1 |
| `automation_completed` | Zincirleme; Sub-Zap muadili. Karmaşık akışı parçalara bölme. | Orchestrator bitişte bir iç sinyal yayar; `signal` trigger mekanizması yeniden kullanılır. | S | 1 |
| `form_submission` | `apps/websites` iskeleti tam buraya oturuyor — bugün boş | Website form modeli + submit view → iç sinyal. | M | 3 |
| `email_inbound` | Gelen e-postayla tetikleme (destek talebi, sipariş bildirimi) | Mail sağlayıcısının inbound webhook'u → mevcut webhook yolu. | S | 3 |
| `signal` genişletmesi: `field_changed` | "Sipariş durumu `hazırlanıyor`→`yolda` olduğunda" — bugün ifade edilemiyor, sadece "güncellendi" var | `pre_save`'de eski değerin anlık görüntüsü, `post_save`'de karşılaştırma. `core/signals.py`'de zaten bir `pre_save` yolu var; deseni oradan al. | M | 1 |
| `manual_bulk` | Bir kişi listesi üzerinde toplu çalıştırma (kampanya) | Manuel trigger'ın liste alan hâli; kuyruk izolasyonu (T8) önkoşul. | M | 3 |

**Mevcut `scheduled` trigger'da kapatılması gereken üç boşluk** (§7 Faz 0):
`one_time` hiç `PeriodicTask` yaratmıyor; `schedule_type='n'` sayacı hiç
uygulanmıyor; ve cron alan sırası yanlış eşleniyor. Detay §7 Faz 0'da.

### 4.2 Koşullar ve evaluator

Bugünkü `ConditionEvaluator` sağlam yazılmış ama dar. Yapılacaklar:

**(a) Tip farkındalığı.** `AutomationCondition.value` bir `CharField`; `gt/lt`
`int()`'e zorluyor. `10.5` kırılır, tarih karşılaştırması imkânsız. Çözüm:
`value` → `JSONField` + `value_type` (`string|number|bool|date|list`). Karşılaştırma
tipe göre seçilir. Göç: mevcut satırlar `string` olarak işaretlenir, davranış korunur.

**(b) Operatör seti.** Eklenecekler:
`gte`, `lte`, `not_contains`, `starts_with`, `ends_with`, `regex`,
`is_empty`, `is_not_empty`, `exists`, `between`, `in_last_n_days`, `changed_from_to`.
Regex için ReDoS koruması şart (girdi kiracıdan geliyor): derleme zaman aşımı veya
`re2` benzeri güvenli motor.

**(c) Nokta yolu.** `data.get(field)` iç içe payload'a ulaşamıyor.
`resolve_path(data, 'customer.address.city')` — §4.5'teki ifade çözümleyicisinin
aynısı, tek bir yerde.

**(d) Gruplama (T2).** Tam ifade ağacı yerine **ayrık normal form** öneriyorum:
`AutomationCondition.group_index` (PositiveInteger) eklenir; **grup içi AND**,
**gruplar arası OR**. `(A AND B) OR (C AND D)` ifade edilebilir hâle gelir.
- *Neden ağaç değil:* mevcut model satır tabanlı; ağaca geçmek `AutomationCondition`
  tablosunu ve tüm serializer'ları yeniden yazmak demek. DNF, kullanıcının pratikte
  ihtiyaç duyduğu ifade gücünün neredeyse tamamını karşılıyor — Zapier Paths'in
  yaptığı da bu — ve göç maliyeti bir alan eklemekten ibaret.
- *Ne zaman yetmez:* üç seviyeli iç içe mantık. O noktada dallanma adımı (§4.4)
  daha okunaklı bir cevap zaten.

**(e) Kısa devre.** `evaluate_conditions()` grubu değerlendirirken bir AND `False`
dönünce grubun kalanını atlar; bir grup `True` dönünce kalan grupları atlar. Bugün
israf, `ai_eval` sonrası **maliyet kontrolü**. AI'lı koşullar değerlendirme sırasında
**daima sona** alınır.

**(f) Eksik alan politikası.** Bugün eksik alan `EvaluationError` → tüm otomasyon
başarısız. Seçenek olmalı: `on_missing: fail | skip_condition | treat_as_null`.
Varsayılan `fail` kalır (sessiz yanlış davranıştan iyidir).

**(g) `ai_eval` operatörü.** §6.3.

### 4.3 Aksiyonlar

| Yeni aksiyon | Neden | Efor | Faz |
|---|---|---|---|
| `http_request` | n8n'in HTTP node'unun 400 entegrasyonun kapatamadığını kapatması gibi. Auth tipleri, retry, **cevabı adım çıktısı olarak yakalama**. | M | 1 |
| `create_record` / `update_record` | Dahili CRM: otomasyon kendi veritabanına yazabilmeli (etiket, not, puan) | M | 3 |
| `telegram` | Türkiye'de yaygın, API ücretsiz ve basit | S | 3 |
| `push` (FCM) | Mobil uygulaması olan zincirler | M | 3 |
| `voice_call` | Netgsm sesli arama; randevu hatırlatmada SMS'ten etkili | S | 3 |
| `slack` / `discord` | İç operasyon bildirimi (stok, vardiya) | S | 3 |
| `ai_generate` | İçerik üretimi — kanca zaten var (§6.4) | S | 4 |
| `ai_classify` | Sınıflandırma/etiketleme; çıktısı sonraki koşulda kullanılır | M | 4 |
| `ai_agent` | Araçlı ajan adımı | L | 4 |
| `mcp_call` | Dış MCP sunucusundaki tool'u çağır (§6.6) | M | 4 |

### 4.4 Kontrol akışı adımları

Bunlar "aksiyon" değil ama aynı adım listesinde yaşarlar — n8n ve Zapier'ın
modeli de bu.

| Adım | Semantik | Efor |
|---|---|---|
| `branch` | Koşula göre iki veya daha fazla dala ayır (Zapier Paths / n8n Switch) | L |
| `filter` | Koşul sağlanmazsa çalıştırmayı burada **durdur** (`skipped`, hata değil) | S |
| `wait` | Süre kadar (`24h`) veya mutlak zamana kadar bekle | M |
| `wait_for_event` | Bir webhook/sinyal gelene kadar bekle, zaman aşımıyla | L |
| `loop` | Liste üzerinde her öğe için alt adımları çalıştır, eşzamanlılık sınırıyla | L |
| `run_automation` | Alt otomasyon çağır (Sub-Zap muadili) | M |
| `human_approval` | Onay bekle; onaysız devam etmez. AI aksiyonlarının guardrail'i (§6.7) | M |
| `set_variable` | Ara değer hesapla/sakla | S |

`wait` ve `wait_for_event`, T4'ün çözümüne (kalıcı çalıştırma durumu) **bağımlı**:
24 saat bekleyen bir çalıştırma, o süre boyunca bir yerde durmak zorunda. Bu yüzden
Faz 1'de `wait` (süreli, `eta` ile) yapılabilir; `wait_for_event` Faz 2'ye kalır.

### 4.5 Veri katmanı — ifade çözümleyicisi

T3'ün çözümü. Tek bir **çalıştırma bağlamı** tanımlanır ve her yerde o kullanılır:

```
{
  "trigger":    { ... tetikleyici payload'ı ... },
  "steps":      { "1": {"status": "success", "output": {...}}, "2": {...} },
  "automation": { "id": ..., "name": ... },
  "tenant":     { ... },  "branch": { ... },
  "now":        "2026-08-03T12:00:00+03:00",
  "vars":       { ... set_variable çıktıları ... }
}
```

Tek bir çözümleyici: `{{trigger.customer.email}}`, `{{steps.2.output.id}}`,
`{{order.total | currency:TRY}}`, `{{now | date:'d MMMM'}}`. Aynı çözümleyici
**üç yeri** besler: aksiyon `config`'i, koşul `field`'ı, şablon gövdesi.

**Karar: kendi sınırlı çözümleyicimizi yazacağız, Jinja2 veya `eval` kullanmayacağız.**
Gerekçe: şablon metni ve alan yolu **kiracıdan gelen güvenilmeyen girdi**. Jinja2'yi
kullanıcı girdisiyle beslemek sunucu taraflı şablon enjeksiyonu (SSTI) yüzeyi açar;
n8n'in JS ifadelerine izin vermesinin bedeli de kalıcı bir sandbox problemi. Bizim
ihtiyacımız kısıtlı: nokta yolu + indeks + filtre listesi. Yaklaşık 200 satır,
tamamen test edilebilir, çalıştırılabilir kod içermez.

Mevcut `TemplateManager.fill()` bunun ilkel hâli; genişletilerek yerine geçer.
`Template.fields`'ın zorunlu beyanı (T9) opsiyonel hâle gelir: beyan edilirse
doğrulama ve varsayılan için kullanılır, edilmezse bağlamdan çözülür.

### 4.6 Gözlemlenebilirlik

| Yetenek | Neden | Nasıl |
|---|---|---|
| `AutomationRun` + `AutomationStepRun` | T4, T12 | Çalıştırma ve adım başına satır: girdi anlık görüntüsü, çıktı, hata, süre, deneme. `AutomationLog` bunun özet görünümüne dönüşür. |
| Çalıştırma geçmişi API'si (sayfalı) | Bugün loglar detay cevabına gömülü ve sınırsız büyüyor | `automations/<id>/runs/` + `runs/<id>/steps/`; `AutomationReadSerializer`'dan `logs` çıkarılır |
| Replay | Zapier'ın en çok kullanılan operasyon özelliği | Bir `Run`'ı saklanan girdiyle yeniden kuyruğa at; yeni `Run`, `replay_of` ile eskiye bağlı |
| Dry-run / test modu | T11; AI'ın `simulate` tool'unun önkoşulu (§6.2) | `RunMode.TEST`: provider'lar gerçek çağrı yapmaz, `{"would_send": {...}}` döner. Provider taban sınıfında tek noktadan. |
| Hata bildirimi | Bugün başarısız otomasyon sessiz | Başarısız `Run` → yöneticiye bildirim (kendi motorumuzla) |
| Metrikler | §9 | Kiracı/otomasyon başına başarı oranı, p95 süre, kanal teslimat oranı, AI maliyeti |

### 4.7 Güvenlik ve dayanıklılık

| Konu | Bugün | Hedef |
|---|---|---|
| Credential (T7) | `JSONField`, düz metin | **Zarf şifreleme**: KMS/env'deki KEK ile sarmalanan kiracı başına DEK, veri Fernet (AES-CBC+HMAC) ile. KEK rotasyonu tüm veriyi yeniden şifrelemeyi gerektirmez. `EncryptedJSONField` olarak alan düzeyinde saydam. |
| OAuth | Yok | Ayrı `ChannelCredential`: `access_token`, `refresh_token`, `expires_at`; süresi dolmadan yenileyen Celery task'i. Instagram/WhatsApp'ın gerçek entegrasyonu bunsuz olmaz. |
| Webhook kimlik doğrulama | `X-Webhook-Secret` başlığı, DB'de eşitlik araması | Gövde üzerinden **HMAC imzası** + zaman damgası + tekrar penceresi. Mevcut secret yöntemi geriye dönük uyumluluk için kalır ama önerilmez. |
| Idempotency (T5) | Yok | `Idempotency-Key` başlığı ya da gövde hash'i → `WebhookDelivery` tablosunda tekil kısıt + TTL. Aynı anahtar ikinci kez gelirse 200 döner, otomasyon **çalışmaz**. |
| Rate limit | Yok | Webhook ucu için kiracı başına istek kotası; kanal başına gönderim kotası (SMS'te doğrudan para) |
| Kuyruk izolasyonu (T8) | Tek kuyruk | En az `default` / `bulk` ayrımı; ileride plan bazlı. `run_automation.apply_async(queue=...)` |
| Retry | Senkron döngü, backoff yok | Celery'nin `autoretry_for` + üstel backoff + jitter; tükenirse ölü mektup kuyruğu ve `Run` durumu `failed_permanently` |
| Secret sızıntısı | `execution_context` her şeyi logluyor | Log yazımında bilinen hassas anahtarların maskelenmesi |

---

## 5. Mimari Kararlar

### 5.1 Karar: adım grafiğine geçiş (T1, T3)

`AutomationAction` doğrusal bir liste. Öneri: **`AutomationStep`** — tüm adım
tiplerinin (aksiyon, dallanma, bekleme, filtre, döngü, AI) ortak taşıyıcısı.

```python
class AutomationStep(BaseModel):
        automation   = FK(Automation, related_name='steps')
        step_type    = CharField(choices=StepTypes)   # action | branch | wait | filter | loop | ai_*
        key          = CharField(max_length=64)       # akışta atıfta bulunulan ad: "1", "send_sms"
        config       = JSONField()                    # tipe özel, şemayla doğrulanır (mevcut desen)

        channel      = FK(Channel, null=True)
        template     = FK(Template, null=True)
        on_failure   = CharField(choices=OnFailureChoices)

        position     = JSONField(null=True)           # canvas koordinatları — frontend'in ihtiyacı
```

Bağlantılar ayrı bir tabloda tutulur; bu, dallanmayı ve birleşmeyi tek bir kavramla
ifade eder:

```python
class AutomationEdge(BaseModel):
        automation  = FK(Automation, related_name='edges')
        from_step   = FK(AutomationStep, related_name='outgoing', null=True)  # null = giriş
        to_step     = FK(AutomationStep, related_name='incoming')
        branch_key  = CharField(max_length=64, blank=True)  # 'true'/'false'/'default'/dal adı
        order       = PositiveIntegerField(default=0)
```

**Neden liste değil grafik:** dallanmayı `order` üzerine oturtmanın her yolu
(dal id'si + sıra numarası) er geç bir grafiği kötü kodlamış olur. Grafiği açıkça
modellemek, hem birleştirmeyi (`merge`) hem paralel dalı hem de canvas'ı bedavaya
getirir. Yürütme yine topolojik sırada, tek iş parçacığında ilerler — paralellik
başta yok, sonra eklenir.

**Neden `AutomationAction` korunmuyor:** korunabilir — `AutomationStep`'in
`step_type='action'` özel hâli olarak. Göç yolu: yeni tablolar eklenir, mevcut
action satırları veri göçüyle step+edge zincirine dönüştürülür, `AutomationAction`
bir sürüm boyunca okunur kalır sonra kaldırılır. **API'de kırılma yok:** yazma
serializer'ı `actions` listesini kabul etmeye devam edip içeride step/edge'e çevirir.

**Geriye dönük uyumluluk sözleşmesi:** mevcut `actions` API'si Faz 1 boyunca
çalışmaya devam eder; yeni `steps` API'si yanına eklenir. Kırılma, ancak tüm
otomasyonlar göç ettikten sonra ve ayrı bir sürümde.

### 5.2 Karar: çalıştırma durum makinesi (T4, T12)

```python
class AutomationRun(BaseModel):
        automation      = FK(Automation, related_name='runs')
        status          = CharField(choices=RunStatus)  # queued|running|waiting|success|failed|skipped|cancelled
        mode            = CharField(choices=RunMode)    # live | test
        trigger_payload = JSONField()
        context         = JSONField()                   # §4.5 çalıştırma bağlamı
        idempotency_key = CharField(max_length=128, null=True, db_index=True)
        replay_of       = FK('self', null=True)
        started_at / finished_at

class AutomationStepRun(BaseModel):
        run        = FK(AutomationRun, related_name='step_runs')
        step       = FK(AutomationStep, null=True, on_delete=SET_NULL)
        status     = CharField(choices=StepStatus)
        attempt    = PositiveIntegerField(default=1)
        input      = JSONField()
        output     = JSONField(null=True)
        error      = TextField(blank=True)
        duration_ms= PositiveIntegerField(null=True)
```

**Memoizasyon sözleşmesi** (Inngest'ten ödünç): bir `StepRun` `success` ise, aynı
`Run` yeniden yürütüldüğünde o adım **çalıştırılmaz**, saklanan `output` bağlama
enjekte edilir. Bu tek kural şunları birden getirir: worker çökmesinden sağ çıkma,
`wait` adımının uygulanabilirliği (çalıştırma askıya alınıp devam ettirilebilir),
ve AI çağrılarının kaza sonucu yeniden faturalandırılmaması.

`AutomationLog` kaldırılmaz; `AutomationRun`'ın özeti olarak yazılmaya devam eder —
mevcut API ve testler kırılmaz. Ama `AutomationReadSerializer`'dan çıkarılıp
sayfalı kendi ucuna taşınır (T12).

### 5.3 Karar: şema kayıt defteri → JSON Schema (AI'ın önkoşulu)

Bugünkü `CONFIG_SCHEMA` sözlüğü JSON Schema'ya çok yakın ama değil:

```python
{"to": {"type": "string", "required": False, "validator": validate_email_wrapped}}
```

Hedef: her trigger/action/step serializer'ı `to_json_schema()` üretsin (draft 2020-12).
`jsonschema` bağımlılığı zaten `requirements.txt`'te — henüz kullanılmıyor.

**Tek kaynak, dört tüketici:**
1. Çalışma zamanı doğrulama (bugünkü `validate_schema`'nın yerine)
2. drf-spectacular / OpenAPI dokümantasyonu
3. **LLM tool tanımı** (`input_schema`) — §6.2
4. **MCP tool listesi** — §6.6
   *(ve dolaylı olarak beşincisi: frontend form üreteci)*

Bu, projenin "şema güdümlü mimari" felsefesinin (`CLAUDE.md` §3.7) doğal devamı ve
AI fazının kilit taşı. Faz 1'deki şema değişikliklerinden **sonra** yapılmalı, yoksa
iki kez yazılır.

**Bir uyarı — mevcut sözleşme korunmalı:** `serializers/validators.py`'deki
"validator reddediyorsa **raise** etmeli" kuralı JSON Schema'ya geçişte de geçerli;
`format` doğrulayıcıları sessizce `False` dönerse aynı sınıf hata geri gelir
(`croniter.is_valid` vakası, `CLAUDE.md` §7).

### 5.4 Karar: sınırlı ifade dili, kod çalıştırma yok

§4.5'te gerekçelendirildi. Bu, bilinçli bir yetenek sınırıdır ve `CLAUDE.md`'ye
kalıcı kural olarak yazılmalı: **kiracıdan gelen hiçbir metin çalıştırılabilir kod
olarak değerlendirilmez.**

---

## 6. AI Aşaması (Faz 2 — Ürün Anlamında)

### 6.1 Üç ayrı AI yüzeyi — karıştırılmamalı

| | A · AI Builder | B · AI Evaluator | C · AI Action / Agent |
|---|---|---|---|
| Ne yapar | Otomasyonu **kurar** | Koşulda **yargı verir** | Adım olarak **iş yapar** |
| Ne zaman çalışır | Tasarım zamanı | Çalışma zamanı, koşul aşamasında | Çalışma zamanı, aksiyon aşamasında |
| Kim tetikler | Kullanıcı (sohbet) | Motor | Motor |
| Maliyet profili | Seyrek, pahalı olabilir | **Her çalıştırmada** — asıl risk | Çalıştırma başına, öngörülebilir |
| Hata maliyeti | Yüksek ama onayla yakalanır | Sessiz yanlış karar — **en tehlikelisi** | Kötü içerik, görünür |
| Olgunluk | Üretime yakın (üretim-doğrula-onar döngüsü) | Dikkatli tasarımla üretime uygun | Sınıflandırma/üretim hazır, **ajan deneysel** |

Bunlar farklı ürünler. Aynı anda yapılmaya çalışılırsa üçü de yarım kalır.
**Sıra: C(ai_generate) → B(ai_eval) → A(Builder) → MCP server → C(ai_agent).**
Gerekçesi: `ai_generate` en ucuz ve kancası hazır (§6.4); `ai_eval` sağlayıcı
soyutlamasını olgunlaştırır; Builder her ikisini de katalogda görebilmelidir.

### 6.2 A · AI Builder — tool calling ile otomasyon kuran

**Önkoşul:** §5.3'teki makine-okunur katalog. Bu olmadan Builder yapılamaz.

**Tool seti** (LLM'e verilecek; her biri mevcut serializer/permission katmanının
üstünde ince bir sarmalayıcı):

| Tool | Döner | Not |
|---|---|---|
| `list_capabilities(kind?)` | Trigger/step/koşul tipleri + JSON şemaları | Katalog büyürse `tool_search` deseniyle isteğe bağlı yüklenir |
| `list_channels(tenant_id)` | Kiracının yapılandırılmış kanalları | AI olmayan kanalı seçemesin diye |
| `list_templates(tenant_id)` | Mevcut şablonlar | |
| `describe_data_source(model)` | Signal trigger için alan listesi ve tipleri | Koşul yazabilmesi için şart |
| `validate_automation(draft)` | `{valid, errors[]}` | Mevcut serializer'ları çalıştırır, **kaydetmez** |
| `simulate_automation(draft, sample)` | Adım adım ne olurdu | §4.6'daki dry-run altyapısı |
| `create_automation(draft)` | Taslak id'si | **`status=draft`**, asla aktif değil |
| `search_examples(query)` | Kütüphaneden benzer otomasyon | Faz 5'teki şablon kütüphanesi |

**Döngü:** taslak üret → `validate_automation` → hatalar geri beslenir → en fazla
N tur → `simulate_automation` → kullanıcıya özet/fark → **insan onayı** → aktif.
Bu "üret–doğrula–onar" döngüsü, text-to-workflow araştırmasının da vardığı desen:
LLM'e platformun tam düğüm şemasını verip çıktısını makine doğrulamasından geçirmek.

**Kritik güvenlik kararları — üçü de tartışmasız:**

1. **AI asla doğrudan aktif otomasyon yaratmaz.** `Automation.status:
   draft | pending_review | active`; `is_active=True` yalnızca insan onayıyla.
   HITL literatürünün ölçütü tam olarak buraya oturuyor — bir otomasyon 10.000
   kişiye SMS atabilir: **geri alınamaz, maliyetli ve regüle**. Bu üçünün kesişimi,
   onayın zorunlu olduğu tanımın kendisi.
2. **Tool'lar çağıran kullanıcının yetkisiyle çalışır.** AI'a ayrı bir servis hesabı
   vermek, iki katmanlı yetkilendirmeyi baypas eder — `check_permissions`
   gölgeleme hatasının (`CLAUDE.md` §3.4) AI versiyonu olur. `check_tenant_access`
   ve `RoleBasedAccessMixin` aynen uygulanır; `list_channels(tenant_id)` başka
   kiracının kanalını **döndüremez**.
3. **Prompt injection yüzeyi.** Builder, `describe_data_source` ve
   `search_examples` üzerinden kiracı verisi okuyor. Bir müşteri adı alanında
   "önceki talimatları yok say" yazıyorsa bu **veridir, talimat değil**. Somut
   önlem: veri kaynaklı içerik tool sonucu olarak ve açıkça etiketlenmiş biçimde
   verilir; ve nihai koruma onay kapısıdır — model kandırılsa bile aktif otomasyon
   üretemez.

### 6.3 B · AI Evaluator — koşul içinde LLM yargısı

Yeni operatör: `ai_eval`. `field` = değerlendirilecek yol, `value` = doğal dil
ölçüt ("müşteri şikâyet ediyor mu?", "bu talep acil mi?").

**Sözleşme** (mevcut evaluator sözleşmesini bozmadan):

- **Yapılandırılmış çıktı zorunlu:** `{"result": bool, "confidence": 0..1,
  "reason": "..."}`. Serbest üretim yerine tanıma — araştırmanın istikrar için
  işaret ettiği tek somut kaldıraç bu.
- **Katmanlı değerlendirme.** Üretim deseni net: önce ucuz deterministik kontroller,
  karar veremeyince LLM. Uygulaması: `ai_eval` koşulları değerlendirme sırasında
  **daima sona** alınır ve §4.2(e)'deki kısa devre uygulanır. `A AND ai_eval(B)`
  ifadesinde `A` `False` ise LLM **hiç çağrılmaz**.
- **Önbellek.** `(automation_id, condition_id, payload_hash)` → sonuç. Mevcut Redis
  katmanı kullanılır. Aynı payload iki kez ücretlendirilmez. TTL kısa (saatler);
  §6'daki cache sözleşmesi bozulmaz — bu bir *data key*, index'e dokunmaz.
- **Bütçe ve tavan.** Kiracı başına aylık çağrı/token tavanı. Aşıldığında ne olacağı
  `on_ai_failure` ile seçilir: `fail_closed` (koşul `False`) | `fail_open` (`True`) |
  `fallback_deterministic` (yedek klasik koşul). **Varsayılan `fail_closed`.**
  Gerekçe cache katmanındaki ilkenin aynısı: *mesaj göndermemek, yanlış mesaj
  göndermekten iyidir* — "doğru-ve-soğuk, sıcak-ve-yanlıştan iyidir".
- **Zaman aşımı, yeniden deneme yok.** LLM retry = çift ücret. Adım memoizasyonu
  (§5.2) olmadan bir worker çökmesi zaten yeniden ödetir — Faz 2 bağımlılığının
  parasal gerekçesi bu.
- **Denetim izi.** Her `ai_eval` kararının modeli, prompt'u, cevabı, token sayısı,
  gecikmesi ve gerekçesi `StepRun`'a yazılır. İki nedenle: "bu müşteriye neden mesaj
  gitmedi?" sorusunun cevabı olmalı; ve KVKK açısından hangi kişisel verinin hangi
  modele gittiği kayıtlı olmalı.
- **Model seçimi.** İkili yargı ve sınıflandırma için küçük/hızlı model yeterli
  (Haiku sınıfı); üretim ve muhakeme için büyük model. Model, kanal
  credential'ında kiracı bazında seçilebilir olmalı.

**Bilinen zaaf, açıkça kabul edilerek:** LLM yargısı çalıştırmalar arasında
tutarsızdır ve sistematik yanlılık taşır. Bu yüzden `ai_eval` **para veya geri
alınamaz eylem üreten dallarda tek karar verici olmamalı**; ya insan onayıyla
(§4.4 `human_approval`) ya da deterministik bir koşulla birlikte kullanılmalı.
Bunu ürün seviyesinde uyarı olarak göstereceğiz.

### 6.4 C · AI Action — kanca zaten var

`engine/template_manager.py` içinde, bugün:

```python
if source == 'ai':
        continue # TO DO
```

Bu, projedeki en ucuz AI teslimi. `Template.fields` girdisi
`{"source": "ai", "prompt": "...", "max_tokens": 200}` olduğunda TemplateManager
AI sağlayıcısını çağırır ve sonucu alan değeri olarak yerleştirir. Şablon motoru,
sağlayıcı soyutlaması ve maliyet kaydı bir kerede test edilmiş olur — Builder'a
geçmeden önce ısınma turu.

`ai_classify` ise **T3'e bağımlı**: sınıflandırma sonucunu sonraki adımın koşulu
okuyamıyorsa aksiyonun bir anlamı yok. Faz 1 tamamlanmadan yapılmamalı.

`ai_agent` (araçlı, döngü kuran ajan) en sona: araştırmanın ortak yargısı, çok
adımlı otonom karar vermenin hâlâ deneysel olduğu. Guardrail'siz yapılmamalı (§6.7).

### 6.5 AI sağlayıcı soyutlaması

**Yeni bir mimari kavrama gerek yok.** `ChannelTypes.AI` eklenir, `AIProvider`
sınıfı yazılır (Anthropic / OpenAI / yerel), credential şeması tanımlanır. Yani
`CLAUDE.md` §3.7'deki kural AI için de aynen geçerli: *yeni kanal = yeni provider +
yeni şema, model değişikliği yok.* Mimari buna zaten hazır — bu, tasarımın erken
doğru kurulduğunun bir ölçüsü.

Kazançlar bedava geliyor: kiracı kendi API anahtarını getirebilir (BYOK), model
seçimi kanal konfigürasyonunda yaşar, ve maliyet kanal bazında raporlanır.

### 6.6 MCP — iki yön

**MCP Server (dışa açılım).** AutoFlow otomasyonlarını dış ajanlara tool olarak
sunar. `manual` trigger'lı her otomasyon zaten bir tool: `initial_data` şeması
`input_schema` olur. §5.3'teki üreteçten sonra bu **neredeyse bedava**. Sonuç:
kullanıcı Claude'a "Beşiktaş şubesinde yarınki randevuları onayla" diyebilir.

**MCP Client (içe alım).** `mcp_call` adımı, dış MCP sunucularındaki tool'ları
çağırır. **Stratejik olarak en yüksek kaldıraçlı madde:** konnektör yazmadan
entegrasyon genişliği. n8n'in 2026'daki yolu tam olarak bu, ve MCP desteği kurumsal
iPaaS seçiminde ayırt edici hâle geldi.

Güvenlik notu: dış MCP sunucusu **güvenilmeyen taraftır**. Tool açıklamaları
prompt injection taşıyabilir; sunucu izin listesi kiracı bazında olmalı ve
`mcp_call` çıktısı diğer adımlara veri olarak, talimat olarak değil akmalı.

### 6.7 Guardrail'ler — özet

| Guardrail | Nereye |
|---|---|
| Aktifleştirme için insan onayı | AI Builder çıktısı (§6.2) |
| Tool izin listesi + maksimum tur + maksimum maliyet | `ai_agent` (§6.4) |
| Yan etkili tool'dan önce onay | `ai_agent`, `human_approval` adımı |
| Kiracı başına AI bütçe tavanı | Tüm AI yüzeyleri |
| `fail_closed` varsayılanı | `ai_eval` (§6.3) |
| Tam denetim izi (prompt/cevap/model/token) | Tüm AI yüzeyleri |
| Kiracı verisi = veri, talimat değil | Builder ve MCP client |
| Dry-run zorunlu ön adım | AI kurulu otomasyonun ilk aktifleşmesi |

---

## 7. Yol Haritası

### Faz 0 — Doğrulama ve Canlı Defektler · 1–2 hafta

Yeni özellik yok. Bugünkü motorun **iddia ettiği şeyi gerçekten yaptığını** kanıtlar.

**F0.1 — Cron alan sırası yanlış eşleniyor. (Canlı defekt.)**
`apps/automations/signals.py:56-64`, cron ifadesini
`minute, hour, day_of_week, day_of_month, month_of_year` sırasıyla okuyor.
Standart cron — ve `croniter`'ın doğruladığı biçim —
`minute hour day_of_month month day_of_week`. Yani 3., 4. ve 5. alanlar yanlış
sütunlara yazılıyor. `0 9 * * 1` (Pazartesi 09:00) `CrontabSchedule`'a
*"her Ocak ayında, her gün 09:00"* olarak giriyor.
Mevcut testler bunu yakalamıyor çünkü hepsi `"0 1 * * *"` kullanıyor — 3., 4. ve 5.
alanların üçü de `*`, dolayısıyla karışık sıra fark edilmiyor.
→ Düzelt + joker olmayan cron'larla regresyon testi.

**F0.2 — `one_time` zamanlama hiç ateşlenmiyor. (Canlı defekt.)**
Aynı fonksiyon, `cron_expression` yoksa erken dönüyor. `one_time` tipi tanım gereği
cron taşımıyor (§`ScheduledTriggerSerializer`), dolayısıyla **hiçbir `PeriodicTask`
yaratılmıyor**: serializer kabul ediyor, kayıt oluşuyor, otomasyon asla çalışmıyor.
→ `ClockedSchedule` + `one_off=True` ile `start_time`'a bağla.

**F0.3 — `schedule_type='n'` sayacı uygulanmıyor.**
`count` doğrulanıyor ama hiçbir yerde azaltılmıyor; `n` fiilen `forever` gibi
davranıyor. → Çalıştırma sayacı `Run` üzerinden; tükendiğinde `PeriodicTask`
devre dışı.

**F0.4 — Webhook sertleştirmesi.** HMAC imzası + zaman damgası + tekrar penceresi;
uç için rate limit. Mevcut secret başlığı geriye dönük uyumluluk için kalır.

**F0.5 — Gerçek Redis doğrulaması.** `./run_tests.sh --redis` (docker izni gerekiyor).
fakeredis Lua'yı gerçekten çalıştırıyor ama gerçek sunucuya karşı bir kez daha
görülmeli.

**F0.6 — `master` ← `develop`.** Yayın dalı üç birleştirme geride.

**Çıkış kriteri:** dört trigger tipinin dördü de uçtan uca ateşlendiği entegrasyon
testiyle kanıtlanmış olacak. Bugün `scheduled` için bu kanıt yok.

### Faz 1 — Motor Derinliği · 4–6 hafta

Hedef: T1, T2, T3, T6, T9. **Sonraki her fazın veri modeli buradan çıkıyor.**

- `AutomationStep` + `AutomationEdge` (§5.1), veri göçü, `actions` API'si korunur
- Adım tipleri: `action`, `filter`, `branch`, `wait` (süreli), `set_variable`
- Çalıştırma bağlamı + ifade çözümleyicisi (§4.5); `TemplateManager` üzerine oturur
- Koşul motoru v2: tip farkındalığı, genişletilmiş operatörler, nokta yolu,
  `group_index` (DNF), **kısa devre**, `on_missing`
- `http_request` aksiyonu (cevabı adım çıktısı olarak yakalar)
- `automation_completed` trigger'ı; `field_changed` sinyal olayı
- Testler: her yeni adım tipi + grafik yürütme sırası + göç doğruluğu

**Çıkış kriteri:** "VIP ise WhatsApp, değilse SMS; 24 saat sonra yanıt yoksa
yöneticiye bildir" **tek otomasyonda** ifade edilebiliyor ve testi var.

**Risk:** grafik göçü en riskli tek kalem. Azaltma: yeni tablolar önce yazılır,
çift yazma bir sürüm boyunca sürer, eski yol okunur kalır.

### Faz 2 — Dayanıklılık ve Gözlemlenebilirlik · 4–6 hafta

Hedef: T4, T5, T8, T11, T12.

- `AutomationRun` + `AutomationStepRun`; adım memoizasyonu (§5.2)
- Idempotency anahtarı + `WebhookDelivery` dedup
- Üstel backoff + jitter, ölü mektup kuyruğu, kalıcı hata durumu
- `wait_for_event` (askıya alınabilir çalıştırma sayesinde artık mümkün)
- Replay ve dry-run (`RunMode.TEST`)
- Çalıştırma geçmişi API'si (sayfalı); `logs` detay cevabından çıkar
- Kuyruk ayrımı (`default` / `bulk`), kiracı başına eşzamanlılık sınırı
- Başarısız çalıştırma bildirimi

**Çıkış kriteri:** çalıştırmanın ortasında worker öldürüldüğünde, yeniden başlatma
tamamlanmış adımları **tekrar çalıştırmadan** kaldığı yerden devam ediyor — testi var.

### Faz 3 — Entegrasyon Genişliği ve Uyum · 4–8 hafta

Hedef: T7 + §3.2. **Gelir kapısı:** İYS ve WhatsApp uyumu olmadan Türkiye'de
üretimde satılamaz.

- Credential zarf şifrelemesi + anahtar rotasyonu
- OAuth credential'ları + otomatik token yenileme
- **İYS ön kontrol kapısı** — SMS/e-posta öncesi izin sorgusu, `skipped_compliance`
  durumu ve denetim kaydı
- **WhatsApp şablon kaydı + 24s pencere durumu + kategori farkındalığı**
  (1 Ekim 2026 fiyat değişikliği öncesi hazır olmalı)
- Yerel SMS sağlayıcıları (Netgsm, İletiMerkezi) provider'ları
- `polling` trigger + `PollCursor`
- Telegram, push, `create_record` aksiyonları
- KVKK: log saklama süresi, maskeleme, silme kaskadı
- `apps/websites`: form gönderimi trigger'ı (bugün boş iskelet)

**Çıkış kriteri:** izni olmayan bir alıcıya ticari ileti göndermek **mümkün değil**
ve bu bir testle kanıtlı.

### Faz 4 — AI · 6–10 hafta

Sıra §6.1'de gerekçelendirildi.

1. **JSON Schema üreteci** (§5.3) — her şeyin önkoşulu
2. `ChannelTypes.AI` + `AIProvider` + maliyet kaydı
3. `ai_generate` — şablonda `source: 'ai'` kancası
4. `ai_eval` — kısa devre, önbellek, bütçe, `fail_closed`, denetim izi
5. `ai_classify` — çıktısı sonraki koşula akar (T3 çözülmüş olacak)
6. **AI Builder** — tool seti, üret-doğrula-onar döngüsü, taslak + onay kapısı
7. **MCP Server** — otomasyonları dışa tool olarak sun
8. **MCP Client** (`mcp_call`) — dış tool'ları çağır
9. `ai_agent` — en son, guardrail'lerle

**Çıkış kriteri:** kullanıcı doğal dille bir otomasyon tarif ediyor; sistem taslak
üretiyor, simüle ediyor, onaydan sonra aktifleşiyor. **Onaysız hiçbir otomasyon
aktif olmuyor** — testi var.

### Faz 5 — Ölçek ve Kurumsal · sürekli

- Otomasyon versiyonlama + geri alma (T10); çalışan sürüm sabitleme
- **Merkezî şablon devralma:** zincir merkezi kurar, şube sınırlı özelleştirir
  (§3.1'deki en savunulabilir farkımız)
- Otomasyon kütüphanesi / hazır senaryolar (sektör bazlı)
- `apps/analytics`: çalıştırma metrikleri, kanal teslimat oranları, AI maliyeti
- Paralel dal yürütme, `loop` eşzamanlılığı
- Çok bölgeli / okuma replikası

---

## 8. Bağımlılık Zinciri

Sıranın rastgele olmadığını gösteren kritik bağlar:

```
Faz 0  cron/one_time düzeltmesi
         └─> zamanlama-tabanlı her şey (T6, wait, polling)

Faz 1  adım grafiği ─┬─> dallanma (T1)
                     ├─> adım çıktısı akışı (T3) ─┬─> ai_classify anlam kazanır
                     │                            └─> http_request cevabı kullanılabilir
                     └─> kısa devreli koşul (T2) ───> ai_eval maliyet kontrolü

Faz 2  Run/StepRun ─┬─> memoizasyon ───> LLM çağrısı çökmede yeniden ödenmez
                    ├─> dry-run ────────> AI Builder'ın simulate tool'u
                    └─> askıya alma ────> wait_for_event, human_approval

Faz 3  credential şifreleme ──> OAuth ──> gerçek WhatsApp/Instagram entegrasyonu

Faz 4  JSON Schema üreteci ─┬─> LLM tool tanımları (Builder)
                            ├─> MCP tool listesi
                            └─> OpenAPI + frontend form üreteci
```

**Tek cümlede:** AI fazının dört ayrı önkoşulu Faz 1 ve 2'de. Sırayı bozup AI'a
erken başlamak, ifade edemeyeceği bir motora tool yazmak; maliyeti kontrol
edilemeyen bir `ai_eval` yayınlamak; ve simüle edilemeyen bir Builder teslim etmek
demektir.

---

## 9. Ölçüm

| Metrik | Neden | Ne zaman |
|---|---|---|
| Çalıştırma başarı oranı (otomasyon/kiracı bazında) | Temel sağlık | Faz 2 |
| Adım p95 süresi + kuyruk bekleme süresi | T8'in etkisi ölçülebilir olmalı | Faz 2 |
| Kanal teslimat oranı (gönderildi/teslim/başarısız) | SMS'te doğrudan para | Faz 3 |
| İYS red oranı | Uyum sağlığı ve yasal risk göstergesi | Faz 3 |
| WhatsApp kategori dağılımı + mesaj başına maliyet | Ekim 2026 fiyat değişikliği | Faz 3 |
| AI karar başına maliyet + önbellek isabet oranı | `ai_eval` bütçesi | Faz 4 |
| AI taslak kabul oranı ve onay öncesi düzenleme sayısı | Builder gerçekten işe yarıyor mu | Faz 4 |
| İş metrikleri: no-show oranı, geri kazanım, yorum sayısı | Müşteriye satılan şey bu | Faz 5 |

---

## 10. Bilinçli Olarak Yapmayacaklarımız

| Yapılmayacak | Neden | Yerine |
|---|---|---|
| 8.000 konnektör | Kaybedilmiş yarış | `http_request` + MCP client |
| Görsel canvas editörü | Frontend işi | Backend grafiği ifade edilebilir kılar (`position` alanı) |
| Kullanıcı kodu çalıştırma (JS/Python) | Çok kiracılı sandbox güvenliği bu aşamada taşınamaz | Sınırlı ifade dili; gerekirse MCP ile dışarıda |
| Jinja2 / `eval` ile şablon | Kiracı girdisiyle SSTI yüzeyi | Kendi sınırlı çözümleyicimiz (§4.5) |
| Kendi LLM'imizi eğitmek/barındırmak | Odak dağıtır | Sağlayıcı soyutlaması + BYOK |
| Tam ifade ağacı koşul motoru | Model yeniden yazımı, marjinal kazanç | DNF (`group_index`) + `branch` adımı |
| Faz 1'den önce AI | §8 | — |

---

## 11. Kaynaklar

**Platform karşılaştırması ve kontrol akışı**
- [Zapier vs Make vs n8n in 2026: Where AI Agents Actually Fit](https://medium.com/@automation.labs/zapier-vs-make-vs-n8n-in-2026-where-ai-agents-actually-fit-1edbbeff85f3)
- [Zapier Agents vs Make AI Agents vs n8n for SMBs (2026)](https://ivconsulting.in/blogs/zapier-agents-vs-make-ai-agents-vs-n8n-for-smbs-2026/)
- [n8n Docs — Core nodes](https://docs.n8n.io/integrations/builtin/core-nodes)
- [n8n Docs — Loop Over Items (Split in Batches)](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.splitinbatches)
- [Zapier — Add branching logic with Paths](https://help.zapier.com/hc/en-us/articles/8496288555917-Add-branching-logic-to-Zap-workflows-with-Paths)
- [Zapier — Set up custom error handling](https://help.zapier.com/hc/en-us/articles/22495436062605-Set-up-custom-error-handling)
- [Zapier — What is replay?](https://help.zapier.com/hc/en-us/articles/19220226086797-What-is-replay)
- [Zapier — How Zapier handles duplicate data](https://help.zapier.com/hc/en-us/articles/8496260269965-How-Zapier-handles-duplicate-data-in-Zap-workflows)
- [Zapier Platform — Trigger (polling vs instant)](https://docs.zapier.com/platform/build/trigger)
- [n8n Docs — Expressions for data transformation](https://docs.n8n.io/data/expressions-for-transformation/)

**Dayanıklı yürütme**
- [Inngest — How Functions Execute (durable execution)](https://www.inngest.com/docs/learn/how-functions-are-executed)
- [Inngest — Durable Execution: The Key to Harnessing AI Agents in Production](https://www.inngest.com/blog/durable-execution-key-to-harnessing-ai-agents)
- [Temporal — Beyond State Machines for Reliable Distributed Applications](https://temporal.io/blog/temporal-replaces-state-machines-for-distributed-applications)
- [Temporal Docs — Workflow Execution overview](https://docs.temporal.io/workflow-execution)

**AI, MCP, tool calling**
- [Anthropic — Tool use with Claude](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)
- [n8n Docs — MCP Server Trigger](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-langchain.mcptrigger)
- [n8n Docs — MCP Client Tool](https://docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.toolmcp)
- [n8n Docs — Tools Agent](https://docs.n8n.io/integrations/builtin/cluster-nodes/root-nodes/n8n-nodes-langchain.agent/tools-agent)
- [Best enterprise iPaaS for MCP and AI workflow automation (2026)](https://frends.com/insights/best-enterprise-ipaas-for-mcp-and-ai-workflow-automation-2026-comparison)
- [Top 15 Agentic AI Trends to Watch in 2026](https://www.firecrawl.dev/blog/agentic-ai-trends)
- [Automation Atlas — AI Agents in Automation (2026)](https://automationatlas.io/guides/ai-agents-in-automation-2026/)
- [Text to Workflow: Build Automation with AI](https://heym.run/blog/text-to-workflow)
- [IJERT — Natural Language-Driven Workflow Generation Using LLM and n8n](https://www.ijert.org/automation-builder-natural-language-driven-workflow-generation-using-large-language-model-and-n8n-ijertv15is050297)

**LLM-as-judge / değerlendirme**
- [Braintrust — What is an LLM-as-a-judge? When to use it (and when to use deterministic evals)](https://www.braintrust.dev/articles/what-is-llm-as-a-judge)
- [Future AGI — LLM-as-a-Judge in 2026: How It Works, When It Fails](https://futureagi.com/blog/llm-as-a-judge/)
- [arXiv — Reliability without Validity: A Systematic, Large-Scale Evaluation of LLM-as-a-Judge Models](https://arxiv.org/html/2606.19544v1)
- [Arize — LLM as a Judge: Primer and Pre-Built Evaluators](https://arize.com/guides/llm-as-a-judge/)

**Guardrail / human-in-the-loop**
- [Galileo — Essential Framework for AI Agent Guardrails](https://galileo.ai/blog/ai-agent-guardrails-framework)
- [StackAI — Human-in-the-Loop AI Agents: Designing Approval Workflows](https://www.stackai.com/insights/human-in-the-loop-ai-agents-how-to-design-approval-workflows-for-safe-and-scalable-automation)
- [A Human-in-the-Loop Playbook for Production Agentic Workflows](https://ideaforgestudios.com/2026/07/17/human-in-the-loop-ai-agents-autonomy-playbook/)

**Türkiye uyum ve kanallar**
- [İleti Yönetim Sistemi (İYS) Nedir? 2026 Güncel Uygulama Rehberi](https://dsrbelgelendirme.com.tr/ileti-yonetim-sistemi-iys-nedir-2026-guncel-uygulama-rehberi/)
- [İYS API ve Teknik Entegrasyon: Geliştiriciler için Kısa Kılavuz](https://makdos.com/tr/blog/iys-ileti-yonetim-sistemi/649363/iys-api-ve-teknik-entegrasyon-gelistiriciler-icin-kisa-kilavuz/)
- [MDP Group — İYS Entegrasyonu Rehberi](https://mdpgroup.com/blog/ileti-yonetim-sistemi-iys-nedir-iys-entegrasyonu-nedir/)
- [Meta — Pricing on the WhatsApp Business Platform](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
- [Blueticks — WhatsApp Business API Pricing in 2026: Categories, Costs, What Changed](https://blueticks.co/blog/whatsapp-business-api-pricing-2026)
- [Wati — WhatsApp API Templates: Types, Pricing & Best Practices](https://www.wati.io/en/blog/whatsapp-api-templates-guide/)
- [iletiMerkezi vs Twilio: Türkiye'ye SMS göndermek için karşılaştırma](https://www.iletimerkezi.com/docs/guides/iletimerkezi-vs-twilio)
- [SaasMetre — Netgsm vs İleti Merkezi 2026](https://saasmetre.com/karsilastirma/netgsm-vs-ileti-merkezi)

**Dikey talep (çok şubeli işletme)**
- [MyTime — Automated Client Messaging for Franchise and Multi-Location Service Businesses](https://get.mytime.com/resource-center/blog/automated-client-messaging-for-franchise-and-multi-location-service-businesses/)
- [MyTime — Best Franchise Management Software for Service Businesses: 2026](https://get.mytime.com/resource-center/blog/best-franchise-management-software-for-service-businesses-2026-guide/)
- [Gleantap — Best Franchise Software for Multi-Location Businesses](https://gleantap.com/best-franchise-software-for-multi-location-businesses/)

**Güvenlik / credential**
- [Google Cloud — Envelope encryption](https://docs.cloud.google.com/kms/docs/envelope-encryption)
- [AWS — Encryption best practices for Secrets Manager](https://docs.aws.amazon.com/prescriptive-guidance/latest/encryption-best-practices/secrets-manager.html)
- [Paragon — How to Handle OAuth Token Refresh and Expiry at Scale](https://www.useparagon.com/blog/oauth-token-refresh-expiry-at-scale)
- [cryptography.io — Fernet (symmetric encryption)](https://cryptography.io/en/latest/fernet/)
- [Prismatic — The Embedded Integration Platform Guide for B2B SaaS](https://prismatic.io/guides/embedded-integration-platform-guide/)
