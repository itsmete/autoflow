# AutoFlow — Engineering Logbook

Zaman damgalı, **append-only** çalışma günlüğü. En yeni girdi en üstte.

**Yazma kuralı:** her girdi *ne yapıldığını*, **neden** yapıldığını ve *nasıl*
yapıldığını içerir. Bir hata ise: kök neden + **denenip başarısız olan yollar ve neden
başarısız oldukları**. Başarısız denemeleri yazmak, başarılı olanı yazmak kadar
değerli — aynı çıkmaza iki kez girmeyi engeller.

Mimari, stil ve kalıcı kurallar burada değil → `CLAUDE.md`.
İleriye dönük plan burada değil → `ROADMAP.md`.

---

## 2026-11-05 — Faz 1.E (1/2): `http_request` aksiyonu ve cevabın adım çıktısına yazılması

**Kim:** OpenCode (worker) · **Branch:** `develop`
**Durum:** 604 test geçiyor (599'dan), 1 beklenen başarısızlık. Görev T-105.

`webhook` zaten HTTP çağrısı yapıyordu ama cevabı **atıyordu**: `_execute_without_channel`
`True` dönüyordu. ROADMAP §4.3'teki `http_request`'in asıl katkısı çağrı değil, cevabı
`context['steps'][key]['output']`'a yazmak — bir sonraki adım
`{{steps.check.output.json.count}}` okuyup dallanabilsin.

### 1. Ne değişti

- `ActionTypes.HTTP_REQUEST` + migration `0010` (yalnızca `choices` değişiyor).
- `serializers/actions/http_request.py`: `url` zorunlu, `method` varsayılan `GET`,
  `headers`/`query_params`/`body` `{}`, `timeout` 10 — webhook şemasıyla aynı desen,
  ama opsiyonel alanlar şemada **default taşıyor**, böylece istemci altı alan yazmak
  zorunda değil ve saklanan config provider'ın okuyacağı şeyle aynı.
- `engine/actions/http_request.py`: `requires_channel = False`. `params` **`None`**
  (`{}` url'e çıplak `?` eklerdi). 4xx/5xx `ExecutionError` — "istek gitti ama sayfa
  hata sayfası" diye sonraki adımı yanlış gövdeyle çalıştırmaktan iyidir.
- `BaseAction`: `execute()` bool sözleşmesini **koruyor**; `_execute_without_channel`
  dict döndürdüyse `_last_output`'a konuyor ve `run_with_output()` çıktıya
  birleştiriyor (`status_code`, `json`, `text`, `headers`). Çıktı sonra sıfırlanıyor —
  instance başka bir adımda kullanılırsa eski cevabı raporlamasın.
- `send_webhook`'a `timeout = config.get('timeout', 10)` eklendi; cevapsız bir webhook
  koşuyu süresiz açık tutmasın.

### 2. Denenip başarısız olan yol: iki modülde `requests.request` patch'lemek

Testte `patch(WEBHOOK_REQUESTS)` ile `patch(HTTP_REQUEST)` **iç içe** açıldı ve HTTP
adımı MagicMock bir cevap aldı (`'MagicMock' >= 400`). Sebep: iki modül de aynı
`requests` nesnesini import ediyor, `requests.request` **tek** bir öznitelik — içteki
patch dıştakini eziyor, çıkışta geri yazıyor. Bu yüzden testte webhook tarafı
`SendWebhook._execute_without_channel` üzerinden patch'leniyor. Kural:
*paylaşılan bir modülün özniteliğini patch'lerken aynı özniteliği hedefleyen ikinci bir
patch'i iç içe açma.*

### 3. Bilinen sınır

`url` şeması webhook'taki `url_validator`'ü kullanıyor (tutarlılık), yani
`https://api.example.com/{{trigger.id}}` gibi **ifade içeren** bir url yazma anında
reddedilir. Şimdilik sorgu parametreleri/header'lar/gövde ifade kabul ediyor; url'de
ifade gerektiğinde validator'ın `{{...}}` gördüğünde geçmesi gerekir — webhook
şemasıyla birlikte tek yerden değiştirilmeli.

---

## 2026-11-05 — Faz 1.D: DAG yürütücüsü ve kontrol akışı adımları

**Kim:** OpenCode (worker) · **Branch:** `develop`
**Durum:** 599 test geçiyor (566'dan), 1 beklenen başarısızlık. Motor saflığı **7 → 5**.

Görev T-104. `AutomationStep`/`AutomationEdge` (T-101) ve serializer'ları (T-102)
yerinde duruyordu ama **hiçbir şey onları yürütmüyordu**: `AutomationOrchestrator`
hâlâ `AutomationAction` listesini okuyordu. Bu giriş grafiği çalışır hâle getiriyor ve
fazın çıkış kriterini (`ROADMAP.md` §7) tek otomasyonda ifade edilebilir kılıyor.

### 1. Neden iki yürütücü aynı dosyada

`execute_actions()` **donduruldu**. `actions` API'si Faz 1 boyunca sözleşme
(§5.1 "geriye dönük uyumluluk sözleşmesi") ve 566 testin 30+'u ona dayanıyor. Yeni yol
yanına eklendi; hangisinin çalışacağına orchestrator karar veriyor:

> Grafik yalnızca **mecbur** olduğunda devralır: `start_step_key` verilmişse, bir adım
> `action` dışında bir tipteyse, `branch_key`'li bir kenar varsa ya da hiç `AutomationAction`
> satırı yoksa. Aksiyonlardan türetilmiş (T-102'deki) düz zincir eskisi gibi
> `execute_actions()`'tan geçiyor — aksi hâlde her mevcut otomasyon için davranış
> değişirdi, karşılığında hiçbir şey kazanmadan.

`test_a_plain_action_chain_still_uses_the_legacy_executor` bunu sabitliyor.

### 2. `engine/executor.py` — `execute_graph()`

- **İş kuyruğu, özyineleme değil.** `deque` + `executed` kümesi: fan-out ve merge
  (elmas) için özel durum yok, birleşen düğüm bir kez çalışıyor.
- **Kenarlar önce çözülüyor.** Bir kenar step nesnesi, step `key`'i ya da `*_step_id`
  taşıyabiliyor (ORM nesne verir, serializer anahtar, istemci id). `_index_edges()`
  hepsini `(from_key, to_key)`'ye indiriyor, yürüyüş bir daha bakmıyor. Sıralama
  `(order, created_at.timestamp())`; `created_at` `None` olabildiği için ham datetime
  karşılaştırması `TypeError` verirdi — sadece çalıştırmanın ortasında.
- **Adım çıktısı:** `context['steps'][key] = {'status', 'output', 'decision'?}`. Bir
  sonraki adım `{{steps.1.output.message}}` ile okuyor. `run_with_output()` bunun için
  var: `bool` "1. adım ne gönderdi" sorusunu cevaplayamaz.
- **`filter`:** koşul sağlanmazsa o **yol** biter, koşu değil. Çıkış kenarları
  kuyruğa girmez, `Decision` izi `decision` alanına yazılır ("neden hiçbir şey
  gönderilmedi" sorusunun cevabı). Koşu durumu yalnızca ardından hiçbir aksiyon
  başarılı olmadıysa `skipped` olur.
- **`branch`:** ilk eşleşen dal kazanır, **yalnızca** `branch_key`'i seçilen anahtara
  eşit kenarlar kuyruğa girer. Yedek sırası: `config['default']` → `default` adlı kenar
  → `false` adlı kenar. Eşleşmeyen dallar "skipped adım" değil: hiç ulaşılmadı.
- **`set_variable`:** değişkenler **sırayla** çözülüyor, böylece `{{vars.a}}` bir
  sonrakinde okunabiliyor. `RESERVED_NAMESPACES` dışındaki adlar ayrıca üst seviyeye
  yazılıyor (`vars.trigger = x` trigger ad alanını gölgeleyemez).
- **`wait`:** uyumuyor. `parse_wait_duration()` `duration` (`"24h"`, `{"resolve_value"}`
  ile) veya `until` (ISO-8601) okuyup `(delay, eta)` döndürüyor, adım `waiting` olarak
  yazılıp `schedule_continuation(next_key, context, delay_seconds, eta)` çağrılıyor.
  Devam eden adımlar **bu koşuda çalışmıyor**; koşu `status='waiting'` ve log
  `pending` oluyor. Uyumak, worker'ı 24 saat meşgul tutmak demekti.
- **Bilinmeyen adım tipi** (`loop`, `ai_*`, `mcp_call`) `ExecutionError` fırlatıyor.
  Sessizce atlamak, etkisi uygulanmamış bir düğümü "başarılı" saymak olurdu.
- `on_failure`: `stop` → kaydedip durur; `continue` → kaydedip devam eder;
  `retry` → 3 deneme, tükenirse `ExecutionError` (eski `_retry` sözleşmesi).

### 3. `engine/actions/base.py` — config ifadeleri ve çıktı

- `prepare()` config'i `resolve_value(config, payload)` ile çözüyor. Bu olmadan bir
  adım yalnızca kendi config'inin **düz metnini** gönderebiliyordu; adımlar arası veri
  akışı tam olarak buna takılıydı.
- `payload['trigger']` ayrıca **düzleştiriliyor**: grafik tam `ExecutionContext`
  veriyor, sağlayıcılar ise `to`/`subject`/`body`/`message`'ı üst seviyeden okuyor.
  Böylece aynı aksiyon eski yoldan da grafik adımından da aynı şekilde çalışıyor.
- Şablon `message` üretiyor; `body` de yoksa/`None` ise `body`'ye de yazılıyor —
  e-posta `body`, WhatsApp/SMS `message` okuyor, tek şablon ikisine de yetiyor.
- `gettext_lazy` gitti: `ExecutionError` mesajları düz string. Eski
  `_("...") % {...}` kalıbı `logger.error(msg, arg)` ile aynı sınıftandı — çeviri
  yüklenmeden biçimlendirme yapmak.

### 4. `engine/executor.py` — `..models` gitti

`AutomationAction`, `ActionTypes`, `OnFailureChoices` import'u tek satırdı ve motora
"Django'nun ne olduğunu biliyor" dedirtmek için yeterliydi. Yerine string sabitler
(`ON_FAILURE_STOP`, `STEP_BRANCH`, ...) ve `getattr` ile duck-typed erişim. Motor artık
`SimpleNamespace` ile de çalışır — testlerin bir kısmı tam olarak bunu yapıyor.

### 5. Orkestratör: devralan zamanlayıcı, enjekte edilen

`wait` adımı bir devam görevi **zamanlamak** zorunda, zamanlama ise Celery demek.
Orkestratörün içinden `..tasks` import etmek saflık ihlalini 6'ya çıkarıyordu (hedef
5'ti). Bu yüzden **bağımlılık yukarıdan enjekte ediliyor**: `run_automation` görevde
`_schedule_continuation(automation_id)` üretip `run(..., schedule_continuation=...)`
veriyor. Motorun Celery'den haberi yok, görev katmanı da tek yerde duruyor.

**Sapma:** görev tanımı "çağrı biçimi birebir aynı kalsın" diyordu; ben
`schedule_continuation` kwarg'ını her iki dalda da geçiyorum. Gerekçe: alternativa
orkestratörün içinden `..tasks` import etmek ve saflık hedefini (7 → 5) kaçırmaktı.
Bugün bu çağrı imzasına birebir bakan bir test yok; `start_step_key` `None` iken
konumsal argümanlar değişmedi.

Devam koşusu, duraklayan koşunun **tüm bağlamını** taşıyor (`steps`, `vars` dahil);
`_execution_context()` bu yüzden yeniden inşa etmiyor — kendinden inşa etmek eski koşuyu
`trigger`'ın içine gömerdi.

### 6. Testler

`tests/test_engine_graph.py` — 33 test: süre ayrıştırma (7), doğrusal grafik + adım
çıktısı akışı (5), `set_variable` (3), `filter` (3), `branch` (4), `wait` ve devam (3),
orkestratör yolu (4), ve fazın **çıkış kriteri** (2, aşağıda).

**Çıkış kriteri testi** (VIP → WhatsApp → 24s bekle → `replied == False` filtresi →
yöneticiye e-posta) koşuyu iki kez çalıştırıyor: ilki 24 saatlik beklemede `waiting`
duruyor ve `apply_async(start_step_key='check_replied')` çağırıyor, ikincisi o bağlamla
devam edip filtreyi değerlendiriyor ve e-postayı gönderiyor. WhatsApp'ın **ikinci kez**
gönderilmediği, ilk koşunun kayıtlarının ikinci koşunun bağlamında durduğu ve taşınan
bağlamın `json.dumps` edilebildiği (Celery `json` serializer) ayrıca doğrulanıyor.

**Saflık:** `base.py` (`gettext_lazy`) ve `executor.py` (`..models`) temizlendi,
`find_framework_imports()` 7 → 5. `test_engine_purity.py` tavanı 5'e indirildi.

### 7. Riskler / açık kalanlar

- Aynı otomasyonda **paralel** iki dal varsa, `branch` sonrası ikisi de aynı bağlamı
  sırayla işliyor; eşzamanlılık yok (§5.1'de kararlaştırıldığı gibi). `wait` bir dala
  girdiğinde diğer dal akmaya devam ediyor — bu bir karar, test edilmedi.
- Duraklayan koşu `AutomationLog`'da `pending` kalıyor; süreç ölürse kimse farkında
  olmuyor. Kalıcı çalıştırma durumu (`AutomationRun`, Faz 2) bunun asıl çözümü.
- `loop`, `ai_*`, `mcp_call`, `run_automation`, `human_approval` adım tipleri bilerek
  yürütülmüyor: `ExecutionError`. Modelde var olmaları "çalışıyorlar" demek değil.

---

## 2026-11-04 — Faz 1.C: ifade motoru ve çalıştırma bağlamı

**Kim:** OpenCode (worker) · **Branch:** `develop`
**Durum:** 565 test geçiyor (472'den), 1 beklenen başarısızlık. Motor saflığı **9 → 7**.

T-102 (`c9cef2d`) ayrıca commit'lendi: step/edge serializer'ları, DAG doğrulaması,
`actions` çift yazması. Bu giriş onun üstüne gelen Faz 1.C'yi anlatıyor.

### 1. Neden kendi çözümleyicimiz

Şablon gövdesi, aksiyon `config`'i ve koşul `field`'ı — üçü de **kiracının yazdığı
metin**, yani güvenilmeyen girdi. Jinja2'yi ya da `eval`'i bunlarla beslemek, sunucu
tarafında şablon enjeksiyonu yüzeyi açmak demek (`ROADMAP.md` §5.4). İhtiyaç ise dar:
nokta yolu + indeks + kapalı bir filtre listesi + `{%if%}`. `engine/expressions.py`
tam olarak bu kadar, ve fazlası değil — dosya bir oturuşta okunabilsin diye kısa.

### 2. `engine/expressions.py` (saf Python)

- `build_execution_context()` — §4.5'teki sözlük. Trigger payload'ı hem `trigger`
  altında hem üst seviyede: yeni yazım `{{trigger.customer.email}}`, ama mevcut her
  şablon `{{customer.email}}` diyor ve o yazımı düşürmek hepsini birden bozardı.
  `automation`/`tenant`/`branch` yalnızca `getattr` ile okunuyor — motor `Automation`
  ya da `Tenant`'ın ne olduğunu bilmemeli (saflık testi).
- `resolve_context_path()` — önce bağlamın tamamı, sonra (yalnızca ilk segment
  `RESERVED_NAMESPACES`'ta değilse) çıplak payload. Rezerve kökler için geri düşme
  **yok**: `{{automation.name}}` payload'daki bir anahtarı okuyamaz, yoksa kullanıcı
  verisi çalıştırma meta verisini taklit edebilirdi.
- Filtreler: `default`, `upper`, `lower`, `strip`, `title`, `length`, `currency`,
  `date`, `json`. Metin filtreleri eksik değerde boş string döner (koşu ölmesin);
  `currency` ve `date` **raise eder** — bozuk bir tutarı `0.00 TRY` diye göstermek
  veri hatasını gizlerdi.
- `date` hem `%Y-%m-%d` hem `YYYY-MM-DD` kabul ediyor; ikincisi insanların gerçekten
  yazdığı şey. `Z` soneki `+00:00`'a çevriliyor (`fromisoformat` 3.11 öncesi onu
  reddediyor).
- `render_string()` — `{%if x%}` blokları önce (içerideki placeholder sonra normal
  çözülüyor), sonra placeholder'lar. Blok iç içe geçmiyor ve bu sessizce bozulmuyor:
  iç blok metin olarak kalıyor, göze görünüyor.
- `resolve_value()` — tek başına duran `{{path}}` **değerdir**, tipini korur
  (`int`, `list`, `bool`, `None`); etrafında metin varsa enterpolasyondur. Böylece
  `{"count": "{{steps.1.output.count}}"}` sağlayıcıya sayı gider, string değil.

**SSTI yüzeyi kapalı, çünkü dil yok.** Yol çözücü yalnızca sözlük anahtarı ve liste
indeksi okuyor, `getattr` **hiç** çağırmıyor. Yani `{{ __class__.__mro__ }}` bir
anahtar aramasıdır ve cevap `MISSING`; `{{ __import__('os') }}` bir çağrı değil, adı
`__import__('os')` olan bir anahtardır. `{%if 1 == 1%}` de `True` olmaz: ifade dilinde
operatör yok, o yüzden blok düşer. Testler bunları ayrı ayrı sabitliyor.

### 3. `TemplateManager` — iki Django import'u gitti

Tip ipucu için `Template` modeli, iki hata mesajı için `gettext_lazy` import
ediliyordu; ikisi de kaldırıldı, dosya artık saflık testinde temiz (9 → 7).

- `fetch()`: ilan edilmiş alan adları artık **nokta yolu** olabilir; iç içe arama
  önce, düz anahtar sonra (gerçekten `a.b` adlı bir anahtar da var olabilir).
- `fill()`: imza `fill(template, resolved_fields, context=None)`. Görev tanımındaki
  `self.template` fikri bu sınıfta yok — şablon her çağrıda parametre olarak geliyor
  ve mevcut çağıranlar (`engine/actions/base.py`, testler) böyle kullanıyor; imzayı
  değiştirmek onları kırardı.
- `render()`: bağlam `{**payload, **config, **fetched}` — ilan edilmiş alan kazanır
  (beyan nereden geldiğine dair bir sözdür), ilan edilmemiş `{{customer.address.city}}`
  ise doğrudan bağlamdan çözülür.

**Davranış notu:** `{%if bonus%}` artık `bonus == ""` iken bloğu düşürüyor. Eski kod
"anahtar var mı" diye bakıyordu. Boş değer ile yokluk arasındaki farkı blok
koşulunda korumak anlamsız: `exists` operatörü zaten `MISSING` üzerinden çalışıyor,
blok ise "göster mi" sorusu.

### 4. `serializers/templates.py` — beyan opsiyonel hâle geldi

Doğrulama placeholder'ı **taban yolu** üzerinden görüyor: `{{total | currency:TRY}}`
→ `total`, `{%if customer.vip%}` → `customer.vip`. Beyan **gerekmiyor** eğer yol
rezerve bir kökten başlıyorsa (`steps`, `trigger`, `now`, ...) ya da noktalı/indeksli
bir yolsa, ve şablon **hiç alan ilan etmiyorsa**. Beyan **gerekiyor** eğer şablon
alan ilan ediyor ve `{{customer}}` gibi çıplak bir ad kullanılıyorsa — alan haritasının
yakaladığı şey tam olarak bu yazım hatası, ve onu sessizce affetmek haritayı işe
yaramaz hâle getirirdi. `RESERVED_NAMESPACES` serializer'da tekrar tanımlanmadı,
`expressions`'tan import edildi: iki liste er geç ayrışır.

`fields` alanı `required=False, default=dict` (yalnızca serializer'da). Modele
`default=dict` eklemek bir migration daha isterdi; `{}` zaten geçerli bir JSON
değeri, kolon NOT NULL kalmaya devam ediyor. **Sonuç:** `Template.objects.create()`
hâlâ `fields` istiyor, API istemiyor.

### 5. Testler

565 test (472'den): `tests/test_engine_expressions.py` 86 test (bağlam, yol çözümleme,
dokuz filtre, placeholder ayrıştırma, blok ve placeholder render'ı, native tip koruma,
SSTI güvenliği, `TemplateManager` entegrasyonu), `TemplateAPITests` +5 (noktalı yol,
rezerve kök, filtre taban yolu, `fields: {}`, `fields` hiç gönderilmemesi) ve
`test_engine_purity.py` +2 (`expressions.py`/`template_manager.py` temiz kalsın;
ihlal sayısı **yalnızca azalabilir** — 9 → 7 tavanı, sabit bir liste gibi çürümez).

---


**Kim:** OpenCode (worker) · **Branch:** `develop`
**Durum:** 409 test geçiyor (385'ten), 1 beklenen başarısızlık (motor saflığı 9'da sabit).

Faz 1.A önce iki commit'e ayrıldı: `34f2a56` (kural motoru v2) ve `519fde5`
(OpenCode/agent geçişi + `.coverage` artık ignore). Böylece grafik işi tek başına
geri alınabilir bir commit olarak duruyor.

### 1. Neden grafik, neden şimdi

`AutomationAction` doğrusal bir liste ve dallanmayı `order` üzerine oturtmanın her
yolu (dal id'si + sıra numarası) sonunda bir grafiği kötü kodlamış oluyor
(`ROADMAP.md` §5.1). `AutomationStep` + `AutomationEdge` ikilisi dallanma,
birleşme ve canvas'ı tek kavramla ifade ediyor: düğüm + kenar.

### 2. Ne eklendi

- `StepTypes` — 12 tip: `action`, `branch`, `wait`, `filter`, `set_variable`,
  `loop`, `run_automation`, `human_approval`, `ai_generate`, `ai_classify`,
  `ai_agent`, `mcp_call`. AI/MCP tipleri bugün sadece etiket; yürütücüleri
  Faz 1.D'de gelecek. Enum'u şimdi açmak, sonradan migration'la choice eklemeyi
  (SQLite'ta tablo yeniden kurulumu) gereksiz kılıyor.
- `AutomationStep` — `automation`, `step_type`, `key`, `action_type`, `config`,
  `channel`, `template`, `on_failure`, `position`. `(automation, key)` üzerinde
  `unique_step_key_per_automation`.
- `AutomationEdge` — `from_step` **null olabilir**: `None` = giriş kenarı. Sentetik
  bir START düğümü, hiç yürümeyecek bir düğümü her sorguda ve her göçte özel
  duruma sokardı.
- `sync_steps_from_actions(automation)` — action listesinden grafiği türetir
  (sil + yeniden kur, `transaction.atomic` içinde). Türetilmiş durum olduğu için
  artımlı değil: sırası değişmiş satırlardan artımlı türetmek, ikisinin
  ayrışmasının en kısa yolu.

### 3. Göç — kopya, taşıma değil

`0009` tabloları kurar, ardından `RunPython(migrate_actions_to_steps)` çalışır.
`AutomationAction` **silinmiyor**; bir sürüm boyunca okunur kalacak. Göç yalnızca
action'ı olup **hiç step'i olmayan** otomasyonlara dokunur — yarıda kalmış bir göç
ya da ikinci çalıştırma grafiği çoğaltamaz. Ters göç `noop`: geri dönüş, yeni
tabloları düşürmekle olur, action satırları hiç değişmediği için veri kaybı yok.

### 4. `get_tenant()` üzerine not

`AutomationStep`/`AutomationEdge` de `AutomationAction` gibi tenant/branch kolonu
taşımıyor; `get_tenant()` **None** dönüyor ve kapsam `automation` üzerinden
okunuyor. `instance.tenant_id` okumak `AttributeError` verir (AGENTS §7) —
testler bunu açıkça sabitliyor (`test_automations_steps.py`).

### 5. Testler

24 yeni test (`tests/test_automations_steps.py`): varsayılanlar, `__str__`,
`position` JSON gidiş-dönüşü, anahtar tekilliği (aynı otomasyonda `IntegrityError`,
farklı otomasyonlarda serbest), giriş kenarı + `true`/`false`/`default` dallanması,
kenar sıralaması, `sync_steps_from_actions` (0/1/3 action, idempotent, action
silinince grafiğin küçülmesi), ve `migrate_actions_to_steps`'in doğrudan testi
(eski satırlar korunuyor, step'i olan otomasyon atlanıyor, ikinci çalıştırma no-op).

### 6. T-102 — grafiği API'ye açmak (`serializers/steps.py`)

Model vardı ama kimse ona ulaşamıyordu: `steps` yazmanın tek yolu Django shell'iydi.
Bu adım grafiği hem okunur hem yazılır hâle getirdi, `actions` listesini bozmadan.

**Çok biçimli adım doğrulaması.** `validate_step_config(step_type, config, ...)` —
trigger/action'lardaki şema deseninin devamı. `action` adımı için `action_type`
zorunlu, `CHANNEL_REQUIRED_ACTIONS` içindeyse kanal zorunlu, kanalın **ve** şablonun
`channel_type`'ı `action_type` ile eşleşmeli, `config` ilgili action şemasından
geçmeli. `filter` ve `branch` koşulları ayrı yazılmadı: `AutomationConditionSerializer`
ile doğrulanıyor, yani otomasyon seviyesindeki koşullarla aynı kurallar. `wait`
`duration` (pozitif int ya da `30s/15m/24h/7d`) veya `until` (ISO-8601 ya da
`{{...}}`) ister. `set_variable` boş olmayan `variables` sözlüğü ister.

`import` yönü bilinçli: `steps.py`, `automations.py`'yi **modül seviyesinde import
etmiyor** — `automations.py` step serializer'ları için onu import ediyor, ters yön
döngü olurdu. Koşul serializer'ı bu yüzden fonksiyon içinde import ediliyor.

**Grafik doğrulaması (`validate_step_graph`).** Tek bir adım geçerli olup bütün
akış yürüyemez olabilir; o yüzden bunlar adım serializer'ında değil, grafiğin
tamamına bakan tek bir fonksiyonda: anahtar tekilliği, kenarların var olan
adımlara işaret etmesi, kendine bağlanma yasağı, dal adımından çıkan kenarın
`branch_key`'inin **bildirilmiş** olması (`default` her zaman serbest), giriş
kenarı zorunluluğu, döngü tespiti (iteratif 3 renkli DFS — özyinelemeli sürüm
derin bir zincirde 400 yerine 500 üretirdi) ve girişten erişilemeyen adım denetimi.
`edges` verilmezse ve akışta dal adımı yoksa zincir **üretiliyor**; dal varsa
açıkça isteniyor, çünkü hangi dala ne bağlanacağını tahmin etmenin doğru bir yolu
yok.

**Çift yazma.** `AutomationWriteSerializer` artık üç alanı da kabul ediyor ve
`_write_flow()` her iki yönü de kapatıyor: `steps` geldiyse grafik yazılıyor,
`actions` gelmediyse action satırları `sync_actions_from_steps()` ile ondan
türetiliyor — yoksa grafikle kurulmuş bir otomasyon **hiç çalışmazdı**, çünkü
bugünkü yürütücü `AutomationAction` okuyor. Yalnızca `actions` geldiyse liste
yazılıp grafik `sync_steps_from_actions()` ile türetiliyor. Güncellemede temsil
değiştirmek iki tarafı da siliyor; eski action satırlarını bırakmak, yürütücünün
az önce değiştirilmiş akışın eski hâlini çalıştırması demekti.

Grafik kalıcı olarak `AutomationReadSerializer`'da: liste ucu da döndürüyor, yani
`actions` bilen bir istemci yazdığı zinciri grafik olarak, grafik yazan bir istemci
dallanmayı okuyabiliyor.

**Cache:** `AutomationStep`/`AutomationEdge` registry'ye `parent: Automation` olarak
eklendi. İkisi de kendi başına cache'lenmiyor ama artık **Automation payload'unun
içinde** serialize ediliyorlar; action satırları için geçerli olan gerekçe onlar
için de geçerli.

**Testler.** 63 yeni test (472 toplam): adım config doğrulaması (action/filter/
branch/wait/set_variable, geçerli + geçersiz her dal), grafik doğrulaması (çift
anahtar, bilinmeyen uç, self-loop, 2 ve 3 düğümlü döngü, erişilemeyen adım, eksik
giriş kenarı, bildirilmemiş `branch_key`, üretilen zincir) ve API entegrasyonu
(legacy `actions` → grafik, dallanan grafik → action satırları, temsil değiştirme +
cache invalidation, şablon bağlama ve `channel_type` uyuşmazlığı).

---

## 2026-08-03 — Faz 1.A: karar katmanı (evaluator v2 + karar izi)

**Kim:** Claude (Opus 5) · **Branch:** `develop`
**Durum:** 381 test geçiyor (326'dan), 1 beklenen başarısızlık. Motor saflığı 10 → 9.

Motorun en zayıf yeri burasıydı: 67 satır, altı operatör, hepsi string karşılaştırması.
"Kural motoru" denen şey bir `filter()` çağrısından az iş yapıyordu.

### 1. Üç davranış değişikliği — açıkça yazılıyor çünkü mevcut anlamı etkiliyor

**(a) Öncelik. `AND` artık `OR`'dan sıkı bağlıyor.** Eski döngü soldan sağa
katlıyordu: `A AND B OR C AND D` → `(((A ve B) veya C) ve D)`. Artık
`(A ve B) veya (C ve D)` — her dilin kullandığı okuma. Koşullar
`logical_operator`'a göre gruplanıyor: `or` yeni grup açar, `and` mevcut grubu
uzatır. **Saf-AND ve saf-OR zincirleri etkilenmiyor**, sadece karışık zincirler.
Eski davranış bilinçli bir tasarım değil, bir katlama döngüsünün yan etkisiydi.

**(b) Kısa devre.** Bir grup ilk `False`'ta durur, tüm küme ilk `True` grupta durur.
Bugün israf; `ai_eval` geldiğinde doğrudan para.

**(c) Bool değil, `Decision`.** `evaluate_conditions` artık her koşulu, gerçek
değerini ve sonucunu taşıyan bir `Decision` döndürüyor. `__bool__` sonucu
döndürdüğü için `if not decision:` çağrı yerleri değişmedi.

### 2. Sıralama — sessiz bir belirsizlik kapatıldı

`AutomationCondition`'ın `Meta.ordering`'i yoktu ve orchestrator `.all()` çağırıyordu.
Gruplama sıraya bağlı olduğu için, bir koşul kümesinin **anlamı** veritabanının o an
hangi sırayla döndürdüğüne bağlıydı. `order` alanı eklendi (serializer listedeki
konumdan türetiyor, API'den yazılamıyor) + `Meta.ordering`.

### 3. `value` CharField → JSONField

`gt`/`lt` iki tarafı da `int()`'e zorluyordu: `10.5` çökme, tarih imkânsız, liste
ancak virgülle birleştirilmiş string. Artık sayı sayı, liste liste, bool bool.

Migration `0007` sırayı önemsiyor: satırlar **sütun hâlâ CharField'ken** JSON metnine
çevriliyor, sonra `AlterField`. Ters sırada Django ilk okumada `json.loads('paid')`
ile patlardı.

**Data migration bilinçli olarak dar:** yalnızca sayılar metinden çıkarılıyor, bir de
`in`/`not_in`/`between` için virgül ayrımı listeye dönüyor. Eski değerin tamamını
`json.loads`'tan geçirmek `"true"` stringini `True` boolean'ına, `"null"`ı `None`'a
çevirirdi — koşulun neyle karşılaştırdığını sessizce değiştirmek. Sayı olarak kalan
bir string zaten çalışıyor (operatörler coerce ediyor); boolean'a dönen bir string
çalışmıyor.

### 4. Yeni yetenekler

| Ne | Örnek |
|---|---|
| Yol çözümleme | `customer.address.city`, `items[1].sku`, `items.1.sku` |
| Joker | `items[*].stock` bir liste üretir |
| Operatörler 6 → 21 | `gte/lte`, `not_contains`, `starts_with`, `ends_with`, `regex`, `not_in`, `between`, `exists`, `is_empty`, `within_last`, `older_than` … |
| Nicelik belirteci | `field='items[*].stock', quantifier='any', operator='lt', value=1` |
| Eksik alan politikası | `on_missing`: `fail` (varsayılan) / `false` / `true` |

**Nicelik belirteci neden ayrı bir sütun:** alternatif `any_eq`, `all_gt`,
`none_contains` gibi operatörlerin kombinatoryal patlamasıydı. Tek bir alan her
operatörle çarpışmadan birleşiyor.

**Zamansal operatörler `now`'ı enjekte ediyor** (`OperatorContext`), saatten
okumuyor: hem test edilebilir hem de ileride bir replay, çalıştırmanın *orijinal*
zamanına karşı değerlendirilebilir.

### 5. Karar izi — 1.A'nın asıl teslimi

`AutomationLog.decision` (migration `0008`) her koşulu, gerçek değerini, sonucunu ve
hangilerinin kısa devreyle atlandığını saklıyor. `Decision.explain()` insan ve model
tarafından okunabilir tek satırlık özetler üretiyor.

Neden önemli: "false" hiçbir soruyu cevaplamıyor; **"320, 500'den büyük değil"**
hem "bu müşteriye neden mesaj gitmedi" sorusunu hem de — lokal model kural yazmaya
başladığında — modelin kendini düzeltmesini sağlayan geri beslemeyi veriyor.

`EvaluationError` artık **kısmi izi taşıyor** (`error.decision`). Yarıda ölen
çalıştırma, muhakemesi en çok merak edilen çalıştırmadır; onu `raise`'e kurban
vermek israftı.

### 6. Bağımlılık yönü çevrildi

`engine/operators.py` operatör **isimlerinin** tek kaynağı; `models.ConditionOperators`
choices'ını ondan kuruyor. Django katmanı motoru bilebilir, motor Django'yu bilemez.
`evaluator.py` artık tamamen saf — saflık ihlali 10'dan 9'a indi. Yeni modüller
(`paths.py`, `operators.py`, `decision.py`) baştan Django'suz yazıldı.

### 7. Yol boyunca yakalananlar

- **`Decision.__bool__` kendi ayağıma dolandı.** Orchestrator'da
  `e.decision.as_dict() if e.decision else None` yazmıştım. Kısmi iz daima
  `result=False` taşıdığı için `if e.decision` **yanlış** dallanıyor ve kurtarılmak
  istenen şeyi atıyordu. `is not None` şart. Test yakaladı.
- **Saflık testimin sanity fixture'ı çürüdü.** `test_climbing_out_of_the_engine_is_a_violation`
  `evaluator.py`'nin `..models` import ettiğine dayanıyordu — 1.A onu temizleyince
  test kırmızıya döndü. Faz 1'in işi tam olarak o dosyaları temizlemek olduğu için,
  ihlal eden gerçek bir dosyaya bağlanan test **başarı anında** çürüyor. Sentetik
  `_escapes_engine` iddiasıyla değiştirildi.
- `PRESENCE_OPERATORS` eksik alan politikasını **atlıyor**: "bu var mı" sorusuna
  "yok, o yüzden cevap vermeyi reddediyorum" demek kötü bir şaka olurdu.
- Sıralama operatörleri karşılaştıramadıklarında **reddediyor** (raise), string
  karşılaştırmasına düşmüyor: `'9' > '10'` stringlerde doğrudur ve asla kastedilen
  şey değildir.
- Regex deseni ve konusu boyut sınırlı. Python'un `re`'sinde timeout yok, kiracıdan
  gelen desene karşı ucuz tek savunma bu.

---

## 2026-08-03 — Faz 0: zamanlayıcı onarımı ve webhook sertleştirmesi

**Kim:** Claude (Opus 5) · **Branch:** `develop`
**Durum:** 326 test geçiyor (285'ten), 1 beklenen başarısızlık (Faz 1 işareti).

`ROADMAP.md` Faz 0'ın uygulaması. Yeni özellik yok — bugünkü motorun *iddia ettiği
şeyi gerçekten yaptığını* kanıtlamak.

### 1. Zamanlama mantığı `signals.py`'den çıkarıldı

Yeni dosya: `apps/automations/scheduling.py`. Üç defektin üçü de bir sinyal
handler'ının içindeydi ve bu, bir hatanın saklanabileceği en iyi yer: oraya ancak
bir model kaydederek ulaşılıyor, dolayısıyla üçü de 285 testlik bir suite'ten sağ
çıktı. Düz fonksiyon hâline gelince doğrudan test edilebiliyorlar.

### 2. F0.1 — Cron alan sırası (canlı defekt)

`signals.py:56` cron ifadesini `minute, hour, day_of_week, day_of_month,
month_of_year` sırasıyla okuyordu. Standart cron — ve `croniter`'ın doğruladığı
biçim — `minute hour day_of_month month day_of_week`. Son üç alan yanlış sütunlara
yazılıyordu: `0 9 * * 1` (Pazartesi 09:00) `CrontabSchedule`'a **"her Ocak ayında,
her gün 09:00"** olarak giriyordu. Hiçbir şey hata vermiyordu; otomasyon sadece
yanlış zamanda çalışıyordu.

**Neden testler görmedi:** tek zamanlama iddiası `"0 1 * * *"` kullanıyordu. Son üç
alanın üçü de `*` olduğunda doğru eşleme ile karışık eşleme **aynı satırı üretir**.
Testin girdisi ayrımı yapamıyorsa test bir şey kanıtlamıyor — `tests/test_scheduling.py`
içindeki her ifade artık joker içermeyen, birbirinden farklı değerler taşıyor.

### 3. F0.2 — `one_time` hiç ateşlenmiyordu (canlı defekt)

Aynı fonksiyon `cron_expression` yoksa erken `return` ediyordu. `one_time` tanım
gereği cron taşımaz. Sonuç: serializer kabul ediyor, satır yazılıyor, **hiçbir
`PeriodicTask` yaratılmıyor**, otomasyon asla çalışmıyor. `ClockedSchedule` +
`one_off=True` ile `start_time`'a bağlandı; naive datetime aware'e çevriliyor,
geçmiş bir zaman uyarı logluyor.

Bu, 2026-07-31'de düzeltilen `config['cron_expression']` KeyError'ının komşusu.
O düzeltme çökmeyi durdurdu ama susturduğu şeyin *ne olduğuna* bakmadı. **Ders:
bir erken `return` bir çökmeyi susturuyorsa, susturulanın ne olduğu araştırılmalı.**

### 4. F0.3 — `n` tipi sayaç uygulanmıyordu

`config['count']` serializer'da doğrulanıp başka hiçbir yerde okunmuyordu; `n`
fiilen `forever` gibi davranıyordu. `AutomationTrigger.run_count` alanı eklendi
(migration `0006`), `record_scheduled_run()` bütçe dolduğunda beat kaydını devre
dışı bırakıyor.

**Denenip vazgeçilen:** sayacı `PeriodicTask.total_run_count`'tan okumak. beat o
alanı kendi cadence'ıyla (`sync_every`) flush eder, dolayısıyla dispatch'ten hemen
sonra okuyan bir worker bayat değer görebilir. Sayımı gerçekten çalışan task'in
içinde yapmak, sayının kesin doğru olduğu tek yer.

**Test yazarken çıkan ikinci tasarım kusuru:** `run_count` kullanıcı tarafından
düzenlenebilir bir satırda yaşıyor, dolayısıyla bayat bir `save()` sayacı eziyordu —
ve ezip ezmemesi instance'ın taze olup olmamasına bağlıydı, yani **belirsizdi**.
Belirsiz semantik iki seçenekten de kötü. Karar: **zamanlayıcıyı yeniden tanımlamak
bütçeyi sıfırlar.** Gerekçe: `AutomationWriteSerializer.update()` zaten tüm
trigger'ları silip yeniden yarattığı için API yolu bunu hâlihazırda yapıyor; ve
alternatif "sayımı koru" değil, "instance'ın elinde ne kaldıysa onu koru" idi.

### 5. F0.4 — Webhook sertleştirmesi

Yeni imza şeması: `X-Webhook-Id` + `X-Webhook-Timestamp` + `X-Webhook-Signature`
(HMAC-SHA256, `"<timestamp>.<ham gövde>"` üzerinden). Secret artık hatta çıkmıyor,
gövde imza kapsamında, ve yakalanan istek `AUTOFLOW_WEBHOOK_REPLAY_WINDOW` (300s)
sonrasında geçersiz. Zaman damgası imzalanan mesajın **içinde**, yani eski bir imza
taze bir zaman damgasıyla eşleştirilemiyor. Karşılaştırma `hmac.compare_digest`.

Eski `X-Webhook-Secret` yolu geriye dönük uyumluluk için duruyor ama gövde hakkında
hiçbir şey kanıtlamıyor ve kaydı sonsuza dek tekrar edilebilir.

Uca `ScopedRateThrottle` eklendi (varsayılan `120/min`) — kimlik doğrulama kötü
çağıranı ancak bir DB sorgusundan *sonra* reddettiği için, throttle'sız bir genel uç
bize bedava iş yaptırmanın yolu.

**Testte çıkan iki tuzak:**
- Bozuk UUID `django.core.exceptions.ValidationError` fırlatıyor, `ValueError`
  değil. Yakalanmayınca kimlik doğrulaması olmayan bir uçta **500** oluyordu.
- `SimpleRateThrottle.THROTTLE_RATES` bir **sınıf değişkeni**, import anında
  `api_settings`'e bağlanıyor. `override_settings(REST_FRAMEWORK=...)` `api_settings`'e
  ulaşıyor ama throttle'a asla ulaşmıyor — testte `patch.object(...,"THROTTLE_RATES",...)`
  şart. (Üretimde sorun yok, ayar zaten import'ta okunuyor.)

### 6. Motor saflık işareti — Faz 1'in hedefi koda yazıldı

`tests/test_engine_purity.py`: `engine/` altında Django/DRF/Celery import'u ve
`engine/` dışına tırmanan göreli import olmadığını AST ile doğruluyor. Bugün
`@unittest.expectedFailure` — **10 gerçek ihlal** var. Faz 1 bunları kapattığında
unittest "unexpected success" raporlar, bu da koşuyu **başarısız** eder ve dekoratörü
silmeye zorlar. Kendini temizleyen bir işaret.

Kural yeni değil, öznesi yeni: `CLAUDE.md` §4 zaten `core` için aynısını söylüyor.

**Scanner'ın kendi hatası:** ilk sürüm `level >= 2`'yi ihlal sayıyordu.
`engine/actions/base.py` bir paket daha derinde olduğu için oradaki
`from ..exceptions import` `engine.exceptions`'a çözülüyor ve tamamen doğru. Seviyeyi
derinliği saymadan saymak 3 yanlış pozitif üretiyordu — ve bir yanlış pozitif, Faz
1'i olmayan bir sorunu "düzeltmeye" yönlendirirdi. `_escapes_engine()` artık modülün
`engine/` altındaki derinliğini hesaba katıyor.

### 7. Yapılamayanlar

- **F0.5** gerçek Redis doğrulaması: `docker ps` izni yok (kullanıcı `docker`
  grubunda değil). `./run_tests.sh --redis` hazır, koşulamadı.
- **F0.6** `master` ← `develop`: yayın dalına birleştirme kullanıcı onayı bekliyor.

---

## 2026-08-03 — Genişleme araştırması ve yol haritası (`ROADMAP.md`)

**Kim:** Claude (Opus 5) · **Branch:** `develop`
**Durum:** Belge yazıldı. Kod değişikliği yok — bu bir plan, uygulama değil.

Görev: "motor nasıl genişletilebilir, sektör ne sunuyor, ne eklenmeli, ve ikinci
aşamada AI'ın tool calling ile otomasyon kurabilmesi için ne gerekir."

### Ne yapıldı

Üç kaynaktan besleniyor: (1) tüm engine/serializer/model katmanının okunması,
(2) Zapier/Make/n8n + dayanıklı yürütme motorları (Temporal, Inngest, Restate)
üzerine ~15 arama/fetch, (3) Türkiye'ye özgü uyum yüzeyi (İYS, KVKK, WhatsApp
politikası).

### Ana bulgu — neden AI önce yapılamaz

AI'ın kurabileceği otomasyon, motorun ifade edebildiğiyle sınırlı. Bugünkü motora
tool calling bağlarsak AI'a "doğrusal bildirim listesi" yazdırmış oluruz. Dahası
`ai_eval`'in maliyeti kontrol edilemez, çünkü koşul motorunda **kısa devre yok** —
LLM'li koşul, önündeki ucuz koşul zaten `False` dönmüşken de çağrılıyor.
Bu yüzden AI, Faz 1 (motor derinliği) ve Faz 2 (dayanıklılık) üstüne oturtuldu;
dört ayrı önkoşulun listesi `ROADMAP.md` §8'de.

### Araştırma sırasında bulunan üç canlı defekt

Bunlar plana değil, Faz 0'a yazıldı — düzeltilmediler, sadece tespit edildiler:

1. **Cron alan sırası yanlış eşleniyor.** `apps/automations/signals.py:56`
   `minute, hour, day_of_week, day_of_month, month_of_year` sırasıyla okuyor;
   standart cron (ve `croniter`'ın doğruladığı biçim) `minute hour day_of_month
   month day_of_week`. `0 9 * * 1` (Pazartesi 09:00), `CrontabSchedule`'a "her Ocak
   ayında her gün 09:00" olarak giriyor.
   **Neden testler yakalamadı:** hepsi `"0 1 * * *"` kullanıyor — 3., 4. ve 5. alan
   üçü de `*`, dolayısıyla karışık sıra görünmüyor. Ders, §7'deki "mock'lanmış
   Redis ile Lua test etmek" satırının aynısı: **testin girdisi ayrımı yapamıyorsa
   test bir şey kanıtlamıyor.**
2. **`one_time` zamanlama hiç ateşlenmiyor.** Aynı fonksiyon `cron_expression`
   yoksa erken dönüyor; `one_time` tanım gereği cron taşımıyor → hiçbir
   `PeriodicTask` yaratılmıyor. Serializer kabul ediyor, kayıt oluşuyor, otomasyon
   asla çalışmıyor. Çözüm `ClockedSchedule` + `one_off=True`.
3. **`schedule_type='n'` sayacı hiç uygulanmıyor.** `count` doğrulanıyor ama hiçbir
   yerde azaltılmıyor; `n` fiilen `forever` gibi davranıyor.

(1) ve (2) doğrudan 2026-07-31'de düzeltilen "`config['cron_expression']` KeyError"
hatasının komşusu — o düzeltme çökmeyi durdurdu ama `one_time`'ın *sessizce hiç
çalışmadığını* görmedi. Erken `return` bir çökmeyi susturduğunda, susturulan şeyin
ne olduğuna bakmak gerekiyormuş.

### Denenip vazgeçilenler

- **Koşul motorunu tam ifade ağacına çevirmek.** `AutomationCondition` satır tabanlı;
  ağaca geçmek tabloyu ve tüm serializer'ları yeniden yazmak demek. Yerine **ayrık
  normal form** (`group_index`: grup içi AND, gruplar arası OR) seçildi — Zapier
  Paths'in ifade gücü, tek alan eklemenin maliyeti. Üç seviye iç içe mantık
  gerektiğinde doğru cevap zaten `branch` adımı, daha derin bir koşul ağacı değil.
- **Şablon motorunu Jinja2'ye taşımak.** Şablon metni kiracıdan gelen güvenilmeyen
  girdi; Jinja2'yi bununla beslemek SSTI yüzeyi açar. n8n'in JS ifadelerine izin
  vermesinin bedeli de kalıcı bir sandbox problemi. Kendi sınırlı çözümleyicimiz
  (nokta yolu + filtre, çalıştırılabilir kod yok) kararlaştırıldı.
- **`AutomationAction`'ı koruyup dallanmayı `order` üzerine oturtmak.** Dal id'si +
  sıra numarası kombinasyonunun her biçimi, er geç bir grafiği kötü kodluyor.
  Grafiği açıkça modellemek (`AutomationStep` + `AutomationEdge`) birleştirmeyi ve
  canvas'ı da bedavaya getiriyor.

---

## 2026-08-01 — Sistem geneli test suite'i (54 → 285 test)

**Kim:** Claude (Opus 5) · **Branch:** `develop`
**Durum:** 285 test geçiyor. 21 yeni bug bulundu ve düzeltildi.

Görev: "sistemde her şey hakkında test olsun, sonra da çalıştırabilelim."
Cache dışındaki hiçbir şeyin testi yoktu. Test yazmak, test edilmeyen kodun büyük
kısmının **hiç çalışmadığını** ortaya çıkardı.

---

### 1. Ne eklendi

| Dosya | Kapsam | Test |
|---|---|---|
| `tests/factories.py` | Ortak fixture kurucuları | — |
| `tests/__init__.py` | Suite genelinde fakeredis'li `cache_service` (aşağıda "neden") | — |
| `tests/test_core.py` | roles, permissions, BaseModel, JWT, pagination, exception handler | 35 |
| `tests/test_channels.py` | provider registry, credential şeması, SMS/e-posta provider, Channel CRUD | 37 |
| `tests/test_automations_engine.py` | evaluator, executor, template_manager, action'lar, orchestrator, signal handler | 53 |
| `tests/test_automations_serializers.py` | şema güdümlü trigger/action validasyonu | 33 |
| `tests/test_automations_api.py` | Automation/Template CRUD, webhook auth, manuel tetikleme | 40 |
| `tests/test_users_auth.py` | login/JWT, kayıt rol kuralları, admin update/delete, tenant/branch CRUD | 33 |
| `run_tests.sh` | Tek komutluk runner; venv'i gerekirse kendisi kurar, `--redis` ile docker'da gerçek Redis | — |

**`tests/__init__.py` neden cache_service'i değiştiriyor:** `core/apps.py` boot'ta
gerçek bir Redis client kuruyor ve `core/signals.py` her model kaydında onun üzerinden
invalidate ediyor. Testlerde bu, her factory çağrısında localhost:6379'a TCP denemesi
demekti — doğru davranış (hata `None`'a düşüyor) ama yavaş ve traceback çöplüğü.
fakeredis'e çevirmek suite'i hermetik yapıyor, **gerçek `CachingService` ve gerçek
Lua yine çalışıyor**.

---

### 2. Bulunan ve düzeltilen bug'lar

Sıralama: en geniş etkiden en dara.

#### 2.1 Güvenlik — tüm kimlik doğrulaması kapalıydı

`config/settings/base.py`:
```python
'DEFAULT_PERMISSON_CLASSES' : [...]   # PERMISSION'da bir 'I' eksik
```
DRF bilinmeyen anahtarları sessizce yok sayar → varsayılan izin `AllowAny` olarak
kaldı. `permission_classes` tanımlamayan **her view anonime açıktı**: tüm automations
app'i (liste, oluşturma, güncelleme, silme), template'ler, manuel tetikleme.

Düzeltince login kırıldı — `LoginView` public olmalıydı ama bunu hiç belirtmemişti;
typo sayesinde tesadüfen çalışıyordu. `permission_classes = [AllowAny]` +
`authentication_classes = []` eklendi. Aynı dosyada `SPECTATULAR_SETTINGS` de
düzeltildi (drf-spectacular hiç okumuyordu).

> **Ders (`CLAUDE.md` §7'ye işlendi):** ayar anahtarı yazım hataları hata vermez,
> sessizce varsayılana düşer. Bu projede üçüncü kez oldu (`CELERY_BORKER_URL`,
> `'django_db'`, şimdi bu). Ayar adı değiştirdiysen davranışı bir testle doğrula.

#### 2.2 Şema güdümlü validasyon hiç çalışmıyordu

`AutomationTriggerSerializer.validate` ve `AutomationActionWriteSerializer.validate`:
```python
trigger_serializer = serializer_cls(data=config)
trigger_serializer.is_valid(raise_exception=True)
data['config'] = trigger_serializer.validated_data
```
`BaseConfigSerializer` **hiç alan tanımlamıyor**. `validate_config` ise `config` adlı
bir alan için field-level hook. Serializer `data=config` ile kurulunca bağlanacak alan
yok → `validate_config` **asla çağrılmıyor** → `validated_data` **boş dict** dönüyor.

İki sonuç:
1. Hiçbir trigger/action config'i doğrulanmıyordu (geçersiz cron, eksik webhook URL'i,
   bozuk e-posta — hepsi kabul).
2. Config'ler **sessizce siliniyordu**: `data['config'] = {}` olarak kaydediliyordu.
   Zamanlanmış bir trigger sonra `config['cron_expression']` üzerinde `KeyError`
   veriyordu — CLAUDE.md'nin "mimarinin belkemiği" dediği katman fiilen yoktu.

Düzeltme: `serializer_cls().validate_config(config)` — `apps/channels`'ın
`validate_credentials` ile yaptığının aynısı. İki app aynı deseni kullanıyordu,
biri doğru biri yanlış.

#### 2.3 Otomasyon motoru çalıştırılamaz durumdaydı

Önceki oturumda orchestrator'ın `related_name` hatası düzeltilmişti; testler geri
kalanını ortaya çıkardı:

- **`AutomationAction.template` alanı hiç yoktu.** `BaseAction.prepare()` her zaman
  `self.action_model.template` okuyordu → her action çalıştırmasında `AttributeError`.
  Migration `0003_..._template` adı yanıltıcı: o `Template` **modelini** yaratıyor,
  action'a FK eklemiyor. Alan eklendi + `0005_automationaction_template`.
- **`provider_class = EmailProvider()`** — instance atanmış, `base.execute` ise
  `self.provider_class()` çağırıyor, yani instance'ı çağırmaya çalışıyor. Dört action
  sınıfında da sınıfa çevrildi.
- **`send(creds, **prepared_data)`** — provider imzası `send(credentials, payload)`,
  yani tek dict. Splat her kanal tabanlı action'da `TypeError` veriyordu.
- **`TemplateManager.fetch`: `resolved['field_name'] = value`** — literal string key.
  Tüm alanlar tek bir `'field_name'` girdisine çöküyordu, `fill()` hiçbir gerçek
  `{{placeholder}}`'ı eşleştiremiyordu. Şablon motoru hiç çalışmamıştı.
- **Executor retry sonsuz döngü.** Sayaç yalnızca `except` içinde artıyordu; raise
  etmeden falsy dönen bir action worker'ı kilitliyordu. `_retry()` olarak ayrıldı,
  sayaç her denemede ilerliyor.

#### 2.4 Channels app'i tamamen ölüydü

`ChannelListCreateView.get` → `self.get_queryset(...)`, `post` → `self.check_permission(...)`.
**İkisi de mixin'de yok** — caching refactor'ünde `get_cached_queryset` /
`check_role_access` olmuşlardı. Her channels isteği `AttributeError` → 500.

Ayrıca view'lar `allowed_roles` tanımlıyordu; mixin yalnızca `allowed_roles_for_list`
okuyor → rol kısıtı hiç uygulanmıyordu. Ve `read_only_fields = '__all__'` DRF'de
`TypeError` veriyor (liste/tuple olmalı) — `ChannelReadSerializer` ve
`TemplateReadSerializer`'da.

#### 2.5 `check_tenant_access` model instance ile çalışmıyordu

Serializer'lar `PrimaryKeyRelatedField` kullanıyor, o da **nesne** döndürüyor.
`str(tenant_obj)` ise `Tenant.__str__` yani **adı** veriyor → UUID ile asla eşleşmiyor.
Sonuç: **owner kendi tenant'ında hiçbir şey oluşturamıyordu** — kanal, otomasyon,
şablon. Üç ayrı çağrı yeri aynı hatayı taşıyordu.

Düzeltme: `_as_id()` normalleştirici — instance / UUID / string hepsini kabul ediyor.
Çağrı yeri başına yamamak yerine fonksiyonun içine koydum; sonraki çağıran da
korunuyor. Ayrıca `user.tenant`/`user.branch` `None` iken çökmek yerine reddediyor.

#### 2.6 Cache'li detail yolu iç içe serializer'ları anlamıyordu

`AutomationReadSerializer.tenant` bir `TenantSerializer` — yani cache'lenen dict'te
`{'id':…, 'name':…}` duruyor. `get_cached_object` bunu id sanıp karşılaştırıyordu →
**otomasyon ve şablon detayını sahibi bile 403 alıyordu.** `core/mixins._nested_id()`
eklendi.

#### 2.7 Kalanlar

- **`ExtendedRefreshToken`** yalnızca `access_token_class` tanımlıyordu, ama
  `RefreshToken.access_token` `for_user`'ı çağırmaz — claim'leri refresh payload'ından
  kopyalar. Yani **login'de dağıtılan access token'da rol/tenant/branch claim'i yoktu**.
  Claim üretimi `add_scope_claims()`'e alındı, iki yol da kullanıyor.
- **`croniter.is_valid` raise etmiyor, `False` dönüyor.** Şema yürüyücüsü yalnızca
  istisna yakalıyor → her cron ifadesi geçerli sayılıyordu.
  `validate_cron_expression` sarmalayıcısı eklendi, kontrat
  `serializers/validators.py` başına yazıldı.
- **`TemplateWriteSerializer.validate`**: `set(re.findall(r'...'), raw_text)` —
  kapanış parantezi yanlış yerde, `TypeError`. Her şablon oluşturma/güncelleme
  patlıyordu. Ayrıca hata mesajı `%(field)s` diyip `{'fields': …}` geçiyordu (KeyError),
  ve `validate_fields` döngü içinde `value`'yu eziyordu.
- **`UserRegisterSerializer`**: super_admin'in tenant'ı yok, `branch.tenant != user.tenant`
  karşılaştırması her şubeyi reddediyordu → super_admin kimseyi bir şubeye kaydedemiyordu.
- **Zamanlanmış trigger sinyali** `config['cron_expression']` ile abone oluyordu;
  `one_time` schedule'da o alan yok → `KeyError`.
- **`EmailProvider.send`** except bloğu `to`/`subject`'i okuyordu ama backend kurulumu
  patladığında bunlar hiç atanmamış oluyordu → gerçek hata `UnboundLocalError` ile
  değişiyordu. Ayrıca yine `logger.error(msg, str(e))` kalıbı.

---

### 3. Denenip vazgeçilen

- **Şema yürüyücüsünü `False` dönen validator'ları da reddedecek şekilde
  değiştirmek.** Cron sorununu çözerdi ama Django validator'ları (`validate_email`
  vb.) `None` döner — "falsy = geçersiz" kuralı hepsini kırardı. Bunun yerine kontrat
  netleştirildi ("reddetmek = raise etmek") ve `croniter` ona uyduruldu.
- **`AutomationAction.template` yerine `getattr(..., None)` koymak.** Migration'sız
  düzelirdi ama şablon motoru (`template_manager.py` + `Template` modeli, ikisi de
  yazılmış) ulaşılamaz kalırdı. Kod zaten alanı bekliyordu; eksik olan şemaydı.
- **Root'taki `test_cache.py`'yi bırakmak.** Geçen oturum "bayat, silmek senin kararın"
  demiştim. Ama `manage.py test` (argümansız) onu toplayıp import hatası veriyor —
  yani "hepsini çalıştır" isteğini fiilen bloke ediyordu. `tests/test_cache_lua.py`
  onun tam üst kümesi olduğu için silindi.

---

### 4. Sonuç

```
öncesi : 54 test — yalnızca cache katmanı
sonrası: 285 test — core, channels, automations (engine + serializer + API), users, tenants
```

Çalıştırma: `./run_tests.sh` (hiçbir servis gerekmez) veya `./run_tests.sh --redis`.

**Kalan boşluklar:**
- `apps/websites`, `apps/analytics` boş iskeletler — test edilecek davranış yok.
- Instagram/WhatsApp provider'larının `send()` yolları registry ve şema düzeyinde test
  edildi, HTTP çağrıları değil (SMS ve e-posta için edildi).
- Gerçek Redis'e karşı hâlâ koşturulmadı (`--redis` bunun için hazır; bu makinede
  docker soketi izni yok).
- `master` hâlâ `develop`'ın gerisinde.

---

## 2026-07-31 (ikinci oturum) — Bulguların düzeltilmesi

**Kim:** Claude (Opus 5) · **Branch:** `develop`
**Durum:** 20 bug düzeltildi, 54 test geçiyor (öncesinde 18 test / 7 fail).

Aşağıdaki girdide çıkarılan tablonun uygulanması. Sıra bilinçli: önce görünürlük,
sonra bağımsız tek satırlıklar, en son birbirine bağlı cache grubu.

---

### 1. Merge (9e48e40)

`feature/caching-service` → `develop`. Conflict yok. `develop` bununla hem caching
katmanını hem automation template engine'ini kazandı. `master` hâlâ merge öncesinde.

---

### 2. Görünürlük — önce bu, çünkü kalan her doğrulama buna bağlı

**`core/exceptions.py`** — `extended_exception_handler` yakalanmamış istisnayı
`logger.exception()` ile view/method/path bilgisiyle logluyor, sonra generic 500
dönüyor. **Neden ilk sırada:** `core/roles.py`'daki `TypeError` aylarca yaşadı çünkü
her hata aynı içeriksiz 500 gövdesine dönüşüyordu. Bunu düzeltmeden diğer
düzeltmeleri doğrulamak körlemesine olurdu.

---

### 3. Bağımsız tek satırlıklar

| Dosya | Değişiklik | Neden |
|---|---|---|
| `core/roles.py:11` | `.get(role, default=0)` → `.get(role, 0)` | `dict.get` keyword argüman almaz. Tüm `/api/v1/users/` uçları 500'dü. |
| `config/settings/base.py` | `CELERY_BORKER_URL` → `CELERY_BROKER_URL` | Yazım hatası hata vermez; Celery sessizce amqp'ye düşüyordu. |
| `config/settings/base.py` | `'django_db'` → `'django-db'` | Alt çizgili hali modül yolu sanılıp `ModuleNotFoundError`. `.delay()` çağıran her uç 500. |
| `config/settings/base.py` | `'POST'` → `'PORT'`, `parse_default=` → `default=` | Denetimde bulunan yeni hata: Postgres port ayarı hiç okunmuyordu (dev SQLite kullandığı için görünmemiş). |
| `requirements.txt` | `requests==2.32.3` eklendi | `apps/channels/providers/*` import ediyor; temiz kurulumda proje açılmıyordu. |
| `requirements-dev.txt` | Yeni: `fakeredis[lua]` | Lua'yı gerçekten çalıştıran test bağımlılığı. |
| `core/caching.py` | Silindi | Bitirilmemiş taslak; gerçek servis `core/cache/service.py`. Git geçmişinde duruyor. |

---

### 4. Signal katmanı

**`core/signals.py` yeniden yazıldı.**

- **Bilinmeyen model toleransı.** `core/cache/__init__.py`'daki tüm registry
  lookup'ları artık `get_model_data()` üzerinden `or {}` ile gidiyor. Receiver'lar
  sender'sız bağlı olduğu için `Session`, `CrontabSchedule`, `PeriodicTasks` gibi
  modellerde de çalışıyorlar; eskisi `None.get()` yapıp `AttributeError` fırlatıyordu.
- **`pre_save` + `post_save` birlikte.** `pre_save` **eski** kapsamı düşürüyor (bir
  güncelleme satırı başka tenant/branch'e taşıyabilir), `post_save` **yeni** kapsamı.
  post_save aynı zamanda INSERT'i kapsıyor — yeni satır hiçbir index'in üyesi olmadığı
  için pre_save'in yapabileceği hiçbir şey ona ulaşamıyordu (DESIGN-02'nin kökü).
- **Create tespiti `instance._state.adding`.** `if not instance.pk` ölü koddu:
  `BaseModel.id`'nin `default=uuid.uuid4` olması pk'yı `__init__`'te dolduruyor.
- **`get_scope_ids()` BaseModel'e taşındı**, `Tenant` ve `Branch` override ediyor.
  Aynı polimorfizm mixin'de çözülmüş, signals'ta çözülmemişti — **aynı bug'ın iki
  yerde yaşayıp birinde düzeltilmesi.** Modele koymak ikinci kez ayrışmasını
  engelliyor. Çağıranlar: `core/signals.py`, `core/mixins.py:get_object`.

**`apps/automations/apps.py`** — `ready()`'deki çıplak DB sorgusu `post_migrate`
sinyaline bağlandı, ayrıca `DatabaseError` yakalayan gecikmeli bir çağrı bırakıldı.
Temiz veritabanında `migrate` artık çalışıyor (baştan sona doğrulandı).

> **Denenip vazgeçilen:** sorguyu tamamen `request_started`'a ertelemek. Django'nun
> "DB access during app initialization" uyarısını da susturacaktı, **ama Celery
> worker'ları `request_started` yaymaz** — sinyal tetikleyicileri worker'da bağlanmaz
> ve otomasyonlar sessizce çalışmaz olurdu. Uyarı kozmetik, sessiz bozulma değil.

---

### 5. Cache katmanı — birlikte, tek seferde

Bu grup atomik: BUG-07/09/10 birbirini maskeliyordu, tek tek düzeltmek sistemi *daha*
bozuk hale getirirdi (örn. sadece Lua'yı düzeltmek, boş index'in `[]` dönüp geçerli
boş liste olarak servis edilmesini aktive ederdi).

**Önce semantik kararı — DESIGN-01'in çözümü.**

Önceki oturumda üç seçenek bırakılmıştı. Seçilen: **(b)'nin doğru uygulaması** —
"index = bir kapsamın *eksiksiz* cevabı" tanımını netleştirip yazma yolunu buna
uydurmak. (a) response-level cache daha büyük bir yeniden yazımdı; (c) liste
cache'inden vazgeçmek mevcut yatırımı çöpe atardı.

Sözleşme (`core/cache/__init__.py` docstring'inde tam hâli):
- **Data key** = tek nesne. Detail ve list yolu yazar.
- **Index set** = bir kapsamın eksiksiz liste cevabı. **Sadece list yolu yazar,
  sadece kendi kapsamının index'ine.**
- Invalidation index'i **siler**, SREM etmez.
- Boş sonuç cache'lenmez (Redis boş set tutamaz → "boş" ile "yok" ayırt edilemezdi).

**Neden bu DESIGN-01'i çözüyor:** `bulk_cache` artık index key'ini **çağırandan**
alıyor. Eskiden her nesne için `resolve_idx_keys()` çağırıp nesnenin uyduğu *tüm*
index'lere yazıyordu — global `users/all` dahil. Bir şube yöneticisinin dar sonucu
böylece super_admin'in "eksiksiz cevabı" olarak yayınlanıyordu. Artık kapsam tek
kaynaktan geliyor: `get_user_scope(user)` hem queryset filtresini hem index'i
belirliyor, ayrışamıyorlar.

**Neden detail fetch index yazmıyor:** tek nesne, bir listenin eksiksizliği hakkında
hiçbir şey söyleyemez. Kendini `users/all`'a eklemesi, "tüm kullanıcılar" iddiasında
bulunan tek üyeli bir set üretirdi ve `bulk_get` onu DB yerine servis ederdi.

**Lua scriptleri:**

| Dosya | Değişiklik |
|---|---|
| `single_set.lua` | Sadece `SETEX`. Eski hali `SADD data_key data_key` çalıştırıyordu: index'i hiç yazmıyor, üstelik SETEX'in yazdığı string'e SADD edip `WRONGTYPE` fırlatıyordu. |
| `bulk_set.lua` | **Yeni.** Index'i `DEL` edip yeniden kuruyor (union değil, replace), her nesneyi `SETEX`+`SADD` ediyor, index'e `EXPIRE` veriyor. Tek script → index asla yarı kurulmuş gözlemlenemez. |
| `bulk_get.lua` | Başta `EXISTS` kontrolü → miss'te `nil`. Üyelerden birinin TTL'i dolmuşsa cevap eksiksiz değildir: index'i `DEL` edip `nil` dönüyor. |
| `single_invalidate.lua` | `SREM` → `DEL`. Bir üyeyi çıkarmak, hâlâ "eksiksizim" diyen yanlış bir set bırakırdı ve yeni oluşturulan satır için zaten hiçbir şey yapmazdı. |
| `bulk_invalidate.lua` | Yok; `AVAILABLE_SCRIPTS`'ten çıkarıldı. Beyan edilip dosyası olmaması `load_scripts()`'i `FileNotFoundError` ile düşürüyordu. |

**`core/cache/service.py`:**
- Scriptler **lazy** yükleniyor (`_sha()` → `load_scripts()`). Eskiden `apps.py`
  servisi kuruyor ama `load_scripts()`'i hiç çağırmıyordu → `scripts={}` → her işlem
  `KeyError` → `handle_script_error` yutuyor → **cache tamamen sessiz no-op.** Lazy
  yükleme aynı zamanda Redis boot'ta kapalıysa Django'nun açılmasını engellemiyor ve
  Redis sonradan geldiğinde kendini iyileştiriyor.
- `load_scripts()` eksik dosyada `MissingScriptError` fırlatıyor — paketleme hatası
  sessizce geçilmemeli.
- `bulk_get` artık **tek** key alıyor. Eskisi iterable alıp `*idxs` ile açıyordu, bu
  yüzden `'users/all'` string'i 9 tek harfli key'e dönüşüyordu.
- `handle_script_error` sınıf içi `staticmethod` dekoratörü olmaktan çıkıp modül
  düzeyine alındı, `functools.wraps` eklendi, log mesajı operasyon adını içeriyor.
- `core/apps.py` Redis client'ını `decode_responses=True` ile kuruyor — yoksa `get()`
  bytes dönüp cache'e giren ile çıkan tip olarak ayrışıyordu.

**`core/cache/__init__.py`:** `Branch` idx_keys `branch/…` → `branches/…`,
`Template` `templates:branch:` → `templates/branch:` (ikisi de kalıcı miss'ti).
Yeni `get_scope_index_key(model, scope, tenant_id, branch_id)` registry'de tanımlı
olmayan kapsam için `None` dönüyor — **key tutarsızlığı artık yanlış cevaba değil,
düz cache miss'e dönüşüyor.**

**`core/mixins.py`:** `get_user_scope()` eklendi (tek kapsam kaynağı). Tanınmayan rol
artık `PermissionDenied` veriyor; eskiden `qs` bağlanmadan kalıp `UnboundLocalError`
→ opak 500 üretiyordu.

---

### 6. Testler sırasında ortaya çıkan yeni bug'lar

Yazılan entegrasyon testleri, denetimde görülmemiş beş hata daha buldu. Hepsi
"cache" başlığı dışında ama motoru fiilen çalışamaz halde tutuyorlardı.

1. **`apps/automations/signals.py:63` — UUID JSON'a serialize edilemiyor.**
   `json.dumps([instance.automation.id, …])` → `TypeError`. **Zamanlanmış tetikleyici
   oluşturmak hâlâ imkânsızdı**, BUG-04 düzeltildikten sonra bile. `str()` eklendi.
   Aynı hata `views.py`'daki iki `run_automation.delay(automation.id, …)` çağrısında
   da vardı (`CELERY_TASK_SERIALIZER='json'`).
2. **`engine/signal_handler.py` yanlış id gönderiyor.** `create_signal_handler(obj.id)`
   — `obj` bir **AutomationTrigger**, ama orkestratör `Automation.objects.get(id=…)`
   yapıyor. Sinyalle tetiklenen her otomasyon `Automation.DoesNotExist`'e düşüp
   "Automation not found" loglayıp sessizce ölüyordu. `obj.automation_id` oldu.
   Ayrıca `model_to_dict()` payload'ı DRF `JSONEncoder`'dan geçiriliyor (aynı UUID
   sorunu orada da vardı).
3. **`engine/orchestrator.py` var olmayan ilişki adları kullanıyor.**
   `automation.automationcondition_set` / `automationaction_set` — modeller
   `related_name='conditions'` / `'actions'` tanımlıyor, dolayısıyla default accessor
   **yok**. Her çalıştırma `AttributeError`'a düşüyordu. **Motor hiç çalışmamıştı.**
4. **`orchestrator.py` son except bloğu hatayı yutuyor.**
   `logger.error("An unexpexted error happened", str(e))` — ikinci argüman
   `%`-format parametresi sanılıyor, mesajda placeholder yok → logging'in kendisi
   patlıyor. Yani (3)'teki `AttributeError` hiç görünmüyordu. `logger.exception()` oldu.
5. **`is_staff` ile "şube kapsamlı kullanıcı" demek.** `is_staff` Django'nun admin
   paneli bayrağı: **staff *rolündeki* kullanıcıda `False`, super_admin'de `True`.**
   `user.is_branch_manager or user.is_staff` iki yönde de yanlıştı. `is_staff_role`
   property'si eklendi; `core/mixins.py`, `core/permissions.py` ve
   `apps/automations/serializers/automations.py` güncellendi.

Ayrıca `apps/users/views.py`: `UserSelfUpdateSerializer` context olmadan kuruluyordu;
`validate()` `self.context['request']`'i okuduğu için geçerli bir şifre değişikliği
`AttributeError` veriyordu.

---

### 7. Test altyapısı — asıl kalıcı kazanım

Denetimin ana dersi: **mock'lanmış Redis, Lua'nın ne yaptığını doğrulayamaz.**
Antigravity'nin 5 testi yeşilken `single_set.lua` hiçbir zaman index yazmadı.

| Dosya | Rol |
|---|---|
| `tests/utils.py` | `CacheTestMixin` — testlere **gerçek** `CachingService` veriyor (fakeredis üzerinde), Celery cache task'lerini inline çalıştırıyor. `AUTOFLOW_TEST_REDIS_URL` verilirse gerçek Redis'e bağlanıyor. |
| `tests/test_cache_lua.py` | **Yeni, 16 test.** Gerçek `.lua` dosyalarını yürütüyor. Her test bulunan bir defect'e karşılık geliyor. |
| `tests/test_cache_integration.py` | **Yeni, 15 test.** Gerçek view + gerçek signal + gerçek Lua. DESIGN-01/02'yi uçtan uca doğruluyor, Redis çöktüğünde uçların çalışmaya devam ettiğini de. |
| `tests/test_cache_service.py` | Yeni imzalara güncellendi; artık ne olduğunu ve **ne olmadığını** (Lua doğrulaması değil) docstring'de söylüyor. |
| `tests/test_api_endpoints.py` | Cache mock'ları kaldırıldı, `CacheTestMixin`'e geçirildi. |
| `tests/test_users_endpoints.py` | `CacheTestMixin`; iki yanlış assertion düzeltildi (`tenant`/`branch` nested serializer, düz UUID değil); `test_user_self_update` gerçekte var olan davranışa (şifre değişimi) göre üç teste bölündü. |

`docker run redis` denendi, **`/var/run/docker.sock` izni yok** (kullanıcı docker
grubunda değil, passwordless sudo yok). PyPI'da `redis-server` binary paketi de yok.
`fakeredis[lua]` Lua'yı `lupa` ile gerçekten yürüttüğü için yeterli oldu — bu
oturumdaki bug'ların çoğunu tek başına o yakaladı. Yine de gerçek sunucuya karşı
doğrulama için `AUTOFLOW_TEST_REDIS_URL` anahtarı bırakıldı.

---

### 8. Sonuç

```
öncesi : 18 test, 7 fail, cache %100 no-op, motor hiç çalışmamış
sonrası: 54 test, 0 fail, temiz DB'de migrate çalışıyor
```

**Doğrulanmayan tek şey:** gerçek bir Redis sunucusu. fakeredis Lua'yı gerçekten
yürütüyor ama %100 aynı değil (özellikle Redis ≥7 script efektleri replikasyonu ve
cluster davranışı). Üretime almadan önce §5'teki `AUTOFLOW_TEST_REDIS_URL` ile bir kez
koştur.

**Sıradaki adımlar:**
1. Gerçek Redis'e karşı test koşusu.
2. `master`'ı `develop` ile senkronla (hâlâ merge öncesinde).
3. `apps/users/views.py`'ın mixin'i kullanıp kullanmayacağına karar ver (registry
   `User`'ı cache'li işaretliyor ama uçlar cache'e uğramıyor).
4. `apps/channels`, `apps/websites`, `apps/analytics` için test yok — automation
   engine'in execution yolu (executor/evaluator/providers) hiç test edilmemiş durumda.
5. `apps/websites` ve `apps/analytics` hâlâ git'e eklenmemiş (untracked).

---

## 2026-07-31 (ilk oturum) — Caching katmanının kurtarılması, denetimi ve testi

**Kim:** Claude (Opus 5) · **Branch:** `develop` (çalışma), `feature/caching-service` (inceleme)
**Durum:** Denetim tamam, kod değişikliği yapılmadı. Düzeltmeler bekliyor.

### 0. Bağlam
Proje uzun süre durmuş, son dönemde Google Antigravity ile caching servisi
geliştirilmiş. Devralma görevi: Antigravity'nin bıraktıklarını okumak, cache
servislerini test etmek, conflict/edge case aramak, kalıcı notları (`CLAUDE.md`)
ve bu logbook'u kurmak.

---

### 1. BULGU: caching kodu `develop`'da yok — kaybolmamış, başka branch'te

**Belirti.** `develop` üzerinde `core/cache/service.py`, `core/signals.py`,
`core/tasks.py`, `core/apps.py` **yok** — ama `core/__pycache__/` içinde bunların
`.pyc`'leri duruyor (27 Tem 07:07–07:50 damgalı). Klasik "kaynak silinmiş" tablosu.

**Nasıl bulundu.**
`core/cache/` dizini sadece `__pycache__` içeriyordu. `.pyc`'nin `co_names`'ini
`marshal` ile okuyunca `core.cache.service`, `single_cache`, `bulk_cache`,
`resolve_idx_keys` isimleri çıktı → kod gerçekten vardı ve derlenmişti.

**Kök neden.** `git reflog`:
```
07:07  commit  3e5e1a7  fix(cache): resolve type mismatch in mixins and hardcoded signals
07:11  commit  fec5f9f  feat(cache): integrate tenant and branch caching via polymorphic mixin
10:21  commit  f56ba21  fix(core): resolve RBAC bypass, UUID comparison, missing properties
10:24  checkout: feature/caching-service -> master     <-- burada
10:25  merge feature/automation-engine  -> 8052eaf
10:29  master -> develop (fast-forward)
```
Caching işi `feature/caching-service` branch'inde commit'lenmiş, **`develop`'a hiç
merge edilmemiş.** 10:24'teki checkout çalışma ağacındaki caching dosyalarını
kaldırdı; `.pyc`'ler `.gitignore`'da olduğu için yerinde kaldı ve kafa karıştırdı.

**Denenip bırakılan yol.** Önce `.pyc`'leri decompile etmeyi düşündüm (makinede
`pycdc`/`uncompyle6` yok, Python 3.12 için de zaten güvenilmez). Gereksiz oldu —
reflog kaynağı zaten sağlam gösteriyordu. **Ders:** kayıp dosyada decompile'a
gitmeden önce `git reflog` + `git fsck --lost-found` bak.

**İkinci bulgu.** `git diff --stat develop feature/caching-service`: `develop`
sadece caching'i değil, **automation template engine'i de** kaçırmış
(`engine/template_manager.py`, `serializers/templates.py`, migration 0003/0004,
`send_sms.py`). 10:25'teki merge, `feature/automation-engine`'in template'lerden
*önceki* haliyle yapılmış.

**Merge güvenliği.** `git merge-tree --write-tree develop feature/caching-service`
→ exit 0, conflict yok. `feature/caching-service` en eksiksiz branch.

---

### 2. Test ortamı kurulumu

Depoda venv yok; makinede Django, redis kurulu değil; Redis sunucusu da yok.

| Deneme | Sonuç |
|---|---|
| `docker run redis:7-alpine` | **Başarısız** — `permission denied ... /var/run/docker.sock`. Kullanıcı docker grubunda değil, passwordless sudo yok. |
| `fakeredis[lua]` (lupa) | **Çalıştı.** `SCRIPT LOAD` + `EVALSHA` gerçekten destekleniyor, yani **gerçek Lua scriptleri gerçekten çalıştırılabildi.** |

Kurulum: scratchpad'de izole venv + `requirements.txt` + `fakeredis[lua]`.
İnceleme için `git worktree add … feature/caching-service` (ana çalışma ağacına
dokunmadan).

**Yol boyunca çıkan engeller (hepsi ayrı birer bug):**
1. `ModuleNotFoundError: apps.websites` — `apps/websites`, `apps/analytics` git'e
   hiç eklenmemiş (untracked). Ana ağaçtan kopyalandı.
2. `ModuleNotFoundError: requests` — `requirements.txt`'te yok ama
   `apps/channels/providers/instagram.py` import ediyor. **Temiz kurulumda proje
   açılmıyor.** (DESIGN-07)
3. `OperationalError: no such table: automations_automationtrigger` —
   `AutomationsConfig.ready()` başlangıçta DB sorgusu atıyor. **Temiz veritabanında
   `migrate` bile çalışmıyor.** (BUG-06) Test için mevcut `db.sqlite3` kopyalandı.

---

### 3. Cache servisi testleri — Probe A (Lua seviyesi)

`scratchpad/probe_cache.py`: gerçek `core/cache/scripts/*.lua` dosyaları fakeredis'e
yüklendi ve `CachingService` uçtan uca çalıştırıldı. **18 kontrolün 10'u başarısız.**

#### BUG-10 — `load_scripts()` çöküyor, ve zaten hiç çağrılmıyor
```
AVAILABLE_SCRIPTS : [single_set, single_get, bulk_get, single_invalidate, bulk_invalidate]
diskte            : [bulk_get, single_get, single_invalidate, single_set]
-> FileNotFoundError: .../scripts/bulk_invalidate.lua
```
Daha kötüsü: `core/apps.py` `ready()` sadece `CachingService(...)` **kuruyor**,
`load_scripts()`'i **çağırmıyor**. Yani `self.scripts == {}`. İlk `get()` çağrısı
`KeyError: 'single_get'` fırlatıyor, `handle_script_error`'ın çıplak
`except Exception`'ı yutuyor ve `None` dönüyor.

**Net sonuç: caching katmanı şu anda %100 no-op.** Her istek DB'ye düşüyor, hiçbir
hata yüzeye çıkmıyor. Sistem "çalışıyor" görünüyor çünkü sessizce hiçbir şey yapmıyor.
*Bu, denetimin en önemli tek bulgusu.*

#### BUG-07 — `single_set.lua` index set'lerine hiç yazmıyor
```lua
for i = 2, #KEYS do
        redis.call("SADD", data_key, data_key)   -- KEYS[i] olmalıydı
end
```
İki ayrı sorun aynı satırda:
1. Index key'i (`KEYS[i]`) hiç kullanılmıyor → index'ler **boş kalıyor**.
2. `data_key` bir satır önce `SETEX` ile **string** yapıldı; string'e `SADD`
   → `WRONGTYPE Operation against a key holding the wrong kind of value`.
   Script orada patlıyor (`SETEX` geri alınmıyor, Lua'da rollback yok).

Gözlem: `set()` sonrası keyspace `['users/u1']`, `users/all` → `set()` (boş).
Doğrusu `redis.call("SADD", KEYS[i], data_key)`.

#### BUG-08 — `bulk_get`'e string geçiliyor, karakter karakter açılıyor
`core/mixins.py` `get_cached_queryset` şunu yapıyor:
```python
data = cache_module.cache_service.bulk_get(cache_key)   # cache_key bir STRING
```
`bulk_get` ise `evalsha(sha, len(idxs), *idxs)`. `evalsha` çağrısı fiilen şu oldu:
```
numkeys=9  keys=['u','s','e','r','s','/','a','l','l']
```
Doğrusu `bulk_get([cache_key])`.

#### BUG-09 — soğuk cache, geçerli boş liste gibi dönüyor
`bulk_get.lua` boş index'te `{}` → Python'da `[]`. `get_cached_queryset` ise:
```python
if data is None:
        return json.loads(self.get_qs_db(user))
```
`[] is None` → False. **Kullanıcıya boş liste servis ediliyor, DB'ye hiç düşülmüyor.**
BUG-07 ile birleşince: index'ler asla dolmadığı için *her* liste ucu boş dönerdi —
eğer BUG-10 sayesinde cache tamamen devre dışı olmasaydı. İki bug birbirini
maskeliyor; sadece birini düzeltmek sistemi *daha* bozuk hale getirir.
**Sıra önemli: BUG-07 + BUG-09 + BUG-10 birlikte düzeltilmeli.**

#### BUG-11 — registry key tutarsızlıkları
| Model | `cache_key` | `idx_keys` | Sonuç |
|---|---|---|---|
| Branch | `branches/` | `branch/all`, `branch/tenant:{}` | mixin `branches/all` arar, yazan `branch/all` → kalıcı miss |
| Template | `templates/` | `templates:branch:{}` (iki nokta) | mixin `templates/branch:{}` arar → kalıcı miss |
| Tenant | `tenants/` | sadece `tenants/all` | owner/branch_manager rolü için index hiç yok → kalıcı miss |

#### BUG-04 — registry'de olmayan model kaydı `AttributeError`
```python
def check_it_cacheable(model_name):
        return REGISTRY['MODEL_DATA'].get(model_name).get('CACHED')   # None.get
```
`core/signals.py` bunu **her model kaydında** çağırıyor (aşağıya bak).

#### DESIGN-04 — index set'lerinde TTL yok, ölü üye temizlenmiyor
`TTL(users/all)` = `-1`. Bir data key'in TTL'i dolduğunda index'ten `SREM`
edilmiyor; ölü üye sonsuza kadar kalıyor. Redis'te sınırsız büyüyen set'ler.

#### Küçük notlar
- `bulk_set()` `None` döndü (pipeline içindeki WRONGTYPE yüzünden) — sessiz başarısızlık.
- `single_invalidate` **doğru çalışıyor** (tek geçen yol). Yazma bozuk, silme sağlam.
- `handle_script_error` bir `staticmethod` nesnesi olarak dekoratör kullanılmış;
  Python 3.10+'ta çalışıyor (staticmethod'lar callable oldu), 3.9'da `TypeError` verirdi.
  Şu an sorun değil ama kırılgan.

---

### 4. Django seviyesi testler — Probe B

`scratchpad/probe_django.py`: gerçek istek yolu, gerçek signal'ler, yutan exception
handler devre dışı. **8 kontrolün 8'i başarısız.**

#### BUG-01 — `/api/v1/users/` uçlarının tamamı 500 (caching ile ilgisiz)
```python
# core/roles.py:11
return ROLE_WEIGHTS.get(role, default = 0)
# TypeError: dict.get() takes no keyword arguments
```
Düz Python hatası. `dict.get` pozisyonel ikinci argüman ister: `.get(role, 0)`.
`get_visible_roles` ve `can_manage` üzerinden **tüm kullanıcı uçlarını** kırıyor.
`tests/test_users_endpoints.py`'daki 7 failure'ın tek kök nedeni bu.
Bunun bu kadar uzun süre fark edilmemesi DESIGN-05'in (traceback yutma) doğrudan sonucu.

#### BUG-02 / BUG-03 — Celery ayarları
```python
CELERY_BORKER_URL   = env('REDIS_URL', ...)   # BROKER yazım hatası -> broker hiç ayarlanmadı
CELERY_RESULT_BACKEND = 'django_db'           # 'django-db' olmalı (tire)
```
`django_db` bir modül olarak çözülmeye çalışılıyor → `ModuleNotFoundError: No module
named 'django_db'`. `bulk_cache.delay()` / `single_cache.delay()` çağıran **her cache'li
uç 500 veriyor**: tenant listesi, branch listesi, tenant detayı, automation listesi,
template listesi.

> Bu, `tests/test_api_endpoints.py`'ın neden geçtiğini de açıklıyor: o testler
> `core.tasks.bulk_cache.delay`'i mock'luyor. **Mock, bozuk ayarı gizledi.**
> Ders: dış bağımlılığı mock'larken, o bağımlılığın *yapılandırmasını* ayrıca
> doğrulayan bir test bırak.

#### BUG-04 (runtime doğrulaması) — global `pre_save` her modeli vuruyor
`core/signals.py`'da `@receiver(pre_save)` **sender belirtmeden** bağlanmış → Django'nun
kendi modelleri dahil her kayıtta çalışıyor.
```
CrontabSchedule.objects.create(...)
  -> post_save -> PeriodicTasks.update_or_create -> pre_save
  -> core.signals.handle_pre_save_invalidation
  -> check_it_cacheable('PeriodicTasks')
  -> AttributeError: 'NoneType' object has no attribute 'get'
```
**Pratik etkisi: zamanlanmış (scheduled) otomasyon oluşturulamıyor.** Aynı şey
`Session`, `ContentType`, `TaskResult` ve registry'ye eklenmemiş her yeni model için
geçerli.

#### BUG-05 — Tenant güncellemek çöküyor
```python
# core/signals.py:15
idxs = resolve_idx_keys(model_name, instance.tenant_id, instance.branch_id)
# AttributeError: 'Tenant' object has no attribute 'tenant_id'
```
`Tenant` registry'de `CACHED: True` ama `tenant_id`/`branch_id` alanları yok.
`core/mixins.py` bu polimorfizmi `getattr(obj, 'tenant_id', obj.id if ... )` ile
çözmüş; **`core/signals.py`'a aynı düzeltme uygulanmamış.** Aynı bug'ın iki yerde
yaşayıp sadece birinde düzeltilmesi klasik örneği.

#### DESIGN-03 — UUID pk, create-guard'ı ölü kod yapıyor
```python
if not instance.pk:
        return   # "yeni kayıt, invalidate edecek bir şey yok"
```
`BaseModel.id = UUIDField(default=uuid.uuid4)` olduğu için `instance.pk` **INSERT'ten
önce bile dolu**. Ölçüm: `Tenant(...).pk` → `UUID('9eec6444-…')`, hiç `save()`
çağrılmadan. Guard hiç tetiklenmiyor; her create fazladan bir `SELECT` + invalidate
turu yapıyor. Create tespiti `post_save`'in `created` bayrağı veya
`instance._state.adding` ile yapılmalı.

---

### 5. Mimari denetim — Probe C (en kritik bulgu)

`scratchpad/probe_index_poisoning.py`: **`single_set.lua` DÜZELTİLMİŞ hâliyle**
(`SADD KEYS[i] data_key`) senaryo simülasyonu. Soru: Lua onarılsa tasarım sağlam mı?

Kurgu: 1 tenant, 2 şube (A/B), 4 kullanıcı.

```
Adım 1: branch-A yöneticisi kullanıcıları listeliyor (soğuk cache)
        kaynak=DB  index=users/branch:bA  dönen: [u1, u2]
        ...ama 'users/all' artık şunu içeriyor: [users/u1, users/u2]

Adım 2: super_admin kullanıcıları listeliyor
        kaynak=CACHE  index=users/all  dönen: [u1, u2]
        beklenen:                              [u1, u2, u3, u4]
```

#### DESIGN-01 — index set'i *üyeliği* kaydeder, *eksiksizliği* değil
`core/tasks.bulk_cache` her nesneyi `resolve_idx_keys()`'in ürettiği **tüm** index
key'lerine yazıyor — global `users/all` dahil. Ama o listeyi üreten sorgu, rolüne
göre daraltılmıştı. Sonuç: **dar kapsamlı bir rol, geniş kapsamlı index'i
zehirliyor.** super_admin 4 kullanıcıdan 2'sini alıyor. Hata yok, 500 yok, log yok —
sadece eksik satırlar, TTL boyunca (1 saat).

Bu, "yanlış yazılmış bir satır" değil, **modelin kendisinin sonucu.** Redis set'i
"bu nesne bu index'e ait" der; "bu index bu sorgunun tam cevabıdır" diyemez.

**Olası yönler (henüz karar verilmedi):**
- (a) Liste cache'ini nesne düzeyinden **response düzeyine** taşı: cache key'i
  `(model, role, tenant, branch, filtre)` üzerinden türet, tüm serialize edilmiş
  cevabı tek key'de sakla. Eksiksizlik key'in tanımına gömülü olur. Invalidation
  kabalaşır ama **doğru** olur.
- (b) Index'i sadece o index'i doldurmaya yetkili sorgu yazsın (super_admin `all`'ı,
  owner `tenant:`'ı…). Kırılgan; yazma yolunun okuma yolunun niyetini bilmesi gerekir.
- (c) Liste cache'inden tamamen vazgeç, sadece nesne cache'i (`get_cached_object`)
  tut. En küçük ve en güvenli adım.

`caching_report.md` (Antigravity) benzer bir noktaya "Çözüm 1: response bazlı cache"
diye değinmiş — bu bulgu o öneriyi somut kanıtla destekliyor.

#### DESIGN-02 — yeni kayıtlar listelerde görünmüyor
Aynı probe, Adım 3: index sıcakken bir `User` oluşturuluyor.
- `bulk_cache` sadece bir liste **okunduğunda** çalışır, create'te değil.
- `pre_save` invalidation'ı DESIGN-03 yüzünden create'i update sanıp `DoesNotExist`
  alıp sessizce dönüyor — `users/all`'ı **invalidate etmiyor**.

Sonuç: yeni kullanıcı/otomasyon/template, TTL dolana kadar liste ucunda **görünmez**.
Bir otomasyon panelinde bu, kullanıcının "kaydettim ama gelmedi" diyeceği senaryo.

---

### 6. Antigravity'nin bıraktıklarının değerlendirmesi

Adil olmak gerekirse — `caching_report.md` kendi işini dürüstçe eleştirmiş ve iki
gerçek sorunu doğru tespit etmiş:
- `get_object`/`get_queryset`'in dict döndürüp `.delete()`'i kırması → **f56ba21'de
  gerçekten düzeltilmiş**: `get_object()` artık model instance döndürüyor,
  `get_cached_object()` ayrı bir metod ve view'lar `put`/`delete`'te `get_object()`
  kullanıyor. Rapor bu düzeltmeden önce yazılmış, bayat.
- `signals.py`'daki hardcoded `'core'` app adı → **düzeltilmiş**; registry artık
  `'parent': ('automations.Automation', 'automation_id')` formatında ve
  `apps.get_model(app_name, model_name)` dinamik çağrılıyor.

Kaçırdıkları, yukarıdaki BUG-01…BUG-11 ve DESIGN-01…08. Ortak paydası şu:
**hiçbiri gerçek Redis'e karşı çalıştırılmamış.** `tests/test_cache_service.py`
`MagicMock` kullanıyor; `evalsha`'nın doğru argümanlarla çağrıldığını doğruluyor ama
Lua'nın ne yaptığını doğrulamıyor. Bu yüzden 5 test de geçiyor, oysa `single_set.lua`
hiçbir zaman çalışmadı. `tests/test_api_endpoints.py` ise cache'i mock'layarak
BUG-02'yi gizledi.

**Kalıcı ders (`CLAUDE.md` §7.5'e işlendi):** Lua/Redis mantığı mock'la test edilemez.
`fakeredis[lua]` gerçek script yürütmesi verir ve bu denetimdeki bug'ların çoğunu
tek başına yakaladı.

---

### 7. Bu oturumda yapılan değişiklikler

| Dosya | Değişiklik | Neden |
|---|---|---|
| `CLAUDE.md` | **Yeni.** Mimari, branch topolojisi, stil rehberi, bilinen tuzaklar tablosu. | Her oturumda sıfırdan keşif yapmayı bitirmek. Branch tuzağı (§2) tek başına saatler yakabilir. |
| `logbook.md` | **Yeniden yazıldı.** Antigravity'nin mimari notları `CLAUDE.md`'ye taşındı; bu dosya artık zaman damgalı çalışma günlüğü. | Bir dosya "referans", bir dosya "geçmiş" olmalı; eskisi ikisini karıştırıyordu. |

**Kaynak kodda değişiklik yapılmadı.** Gerekçe: bulguların çoğu birbirine bağlı
(BUG-07/09/10 birlikte düzeltilmeli, yoksa sistem daha bozuk hale gelir) ve
DESIGN-01 mimari bir karar gerektiriyor. Önce tablo net olsun, sonra düzeltme.

Denetim betikleri (kalıcı değil, scratchpad'de):
`probe_cache.py` (Lua), `probe_django.py` (istek yolu), `probe_index_poisoning.py`
(tasarım). Düzeltmelere geçilirse bunlar `tests/` altına regression testi olarak
taşınmalı.

---

### 8. Sıradaki adımlar (önerilen sıra)

1. **`feature/caching-service` → `develop` merge.** Conflict yok; `develop` şu an
   hem caching'i hem template engine'i kaçırıyor. Bunu yapmadan hiçbir düzeltme
   doğru branch'e inmez.
2. **Tek satırlık ölümcüller** (bağımsız, düşük risk, yüksek getiri):
   BUG-01 (`roles.py`), BUG-02 + BUG-03 (celery ayarları), DESIGN-07 (`requests`).
3. **DESIGN-05** — `extended_exception_handler`'a `logger.exception()` ekle.
   *Önce bu*, çünkü sonraki her düzeltmenin doğrulanması buna bağlı.
4. **BUG-04 + BUG-05 + BUG-06** — signal katmanı: `pre_save`'i sender'lı bağla veya
   registry lookup'ını `.get(model_name) or {}` ile güvene al; `Tenant`/`Branch`
   polimorfizmini `core/signals.py`'a da uygula; `ready()`'den DB sorgusunu kaldır.
5. **BUG-07 + BUG-09 + BUG-10 + BUG-11 birlikte** — cache yazma yolu. Bu grup
   atomik: tek tek düzeltilirse ara durumlar üretimde daha kötü davranır.
6. **DESIGN-01/02 için karar** — liste cache stratejisi (a/b/c seçeneklerinden biri).
   Bu bir mimari karar; kod yazmadan önce kararlaştırılmalı ve buraya gerekçesiyle
   yazılmalı.
7. Probe'ları `tests/` altına gerçek regression testi olarak taşı.

---

## 2026-07-27 — Antigravity: caching servisi (`feature/caching-service`)

*Geriye dönük olarak commit'lerden ve `caching_report.md`'den derlendi.*

- `06:56` branch `feature/automation-templates`'ten açıldı.
- `ab3c242` caching servisi, Redis Lua scriptleri, obje erişim mixin'inin refactor'ü.
- `f943c9e` GitHub senkronizasyonu (WIP).
- `3e5e1a7` mixins'teki tip uyuşmazlığı ve signals'taki hardcoded app adı düzeltildi.
- `fec5f9f` polimorfik mixin üzerinden tenant + branch caching entegrasyonu.
- `f56ba21` RBAC bypass'ı (`check_permissions` → `check_role_access`), UUID/string
  karşılaştırması, eksik `is_branch_manager` property'si; ilk test suite'i eklendi.
- `10:24` branch **merge edilmeden** bırakıldı; `master`/`develop` başka bir hatta gitti.

Antigravity'nin kendi analiz raporu: `caching_report.md` (kısmen bayat — işaret ettiği
iki sorun f56ba21'de düzeltilmiş, bkz. yukarıda §6).
