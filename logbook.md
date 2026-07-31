# AutoFlow — Engineering Logbook

Zaman damgalı, **append-only** çalışma günlüğü. En yeni girdi en üstte.

**Yazma kuralı:** her girdi *ne yapıldığını*, **neden** yapıldığını ve *nasıl*
yapıldığını içerir. Bir hata ise: kök neden + **denenip başarısız olan yollar ve neden
başarısız oldukları**. Başarısız denemeleri yazmak, başarılı olanı yazmak kadar
değerli — aynı çıkmaza iki kez girmeyi engeller.

Mimari, stil ve kalıcı kurallar burada değil → `CLAUDE.md`.

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
