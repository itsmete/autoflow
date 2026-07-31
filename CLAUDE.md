# CLAUDE.md — AutoFlow

Kalıcı proje rehberi. Her oturumda önce bunu oku. Zaman damgalı değişiklik geçmişi
ve hata çözümleri için `logbook.md`'ye bak — bu dosya "ne olduğu", logbook "ne yaptığımız".

---

## 1. Proje Nedir

AutoFlow, işletmeler ve zincirler için çok kiracılı (multi-tenant) bir **otomasyon ve
orkestrasyon motoru** — Zapier/Make benzeri, Django tabanlı bir backend. İş akışları
şu zincir olarak tanımlanır:

```
Trigger  ->  Condition  ->  Action(lar)
```

### Teknoloji

| Katman | Seçim |
|---|---|
| Framework | Django 6.0.2 + DRF 3.16 |
| Async | Celery 5.6 + django-celery-beat + django-celery-results |
| Auth | JWT (`djangorestframework_simplejwt`) |
| DB | Prod: PostgreSQL · Dev: SQLite (`db.sqlite3`) |
| Cache | Redis + Lua (`evalsha`) — read-through, index-set tabanlı, bkz. §6 |
| Validation | JSON Schema (`jsonschema`) — şema güdümlü dinamik config |
| Docs | drf-spectacular |

---

## 2. Branch Topolojisi

`feature/caching-service` **2026-07-31'de `develop`'a merge edildi** (9e48e40).
`develop` artık caching katmanını ve automation template engine'i içeriyor; en güncel
branch odur. `master` hâlâ merge öncesi 8052eaf'te — birleştirilmesi gerekiyor.

Tarihsel not, çünkü tekrar olabilir: caching işi Temmuz'da `feature/caching-service`
üzerinde yapılmış ama merge edilmemişti. `develop`'a checkout edildiğinde
`core/cache/` boş görünüyor, sadece `__pycache__` artıkları kalıyordu ve kod
silinmiş gibi duruyordu. **Bir dosyanın "kaybolduğunu" düşündüğünde önce
`git reflog` ve `git branch -a`'ya bak** — decompile etmeye kalkma.

---

## 3. Mimari

### 3.1 `core/models.BaseModel`
Her model bundan türer:
- `id` — **UUIDField, `default=uuid.uuid4`** (bu detay önemli, bkz. §6 BUG-07)
- `created_at` / `updated_at`
- `is_active` — soft-delete / toggle
- `get_tenant()` — branch üzerinden tenant'a tırmanan yardımcı

### 3.2 Multi-tenancy (`apps.tenants`)
`Tenant` (üst düzey müşteri) → `Branch` (şube). Modeller isteğe bağlı olarak
ikisine de bağlanır. Yetkilendirmenin tamamı bu hiyerarşi üzerinden yürür.

### 3.3 Roller (`core/roles.py`)
Ağırlık tabanlı hiyerarşi: `super_admin(4) > owner(3) > branch_manager(2) > staff(1)`.
`get_visible_roles(role)` bir rolün görebileceği alt rolleri döndürür.

### 3.4 Erişim kontrolü — iki katman
1. **DRF permission sınıfları** (`core/permissions.py`) — `IsSuperAdmin`, `IsOwner`,
   `IsSameTenant`, `IsSuperAdminOrOwner`. View'a girişte çalışır.
2. **`RoleBasedAccessMixin`** (`core/mixins.py`) — queryset'i role göre daraltır ve
   obje düzeyinde `check_tenant_access(user, tenant_id, branch_id)` uygular.

> **Tarihsel not:** Mixin'deki metodun adı bir dönem `check_permissions` idi. Bu, DRF
> `APIView.check_permissions`'ı **override edip permission_classes'ı tamamen devre
> dışı bırakıyordu** — sessiz bir RBAC bypass'ı. `check_role_access` olarak yeniden
> adlandırıldı (f56ba21). **Mixin içinde asla DRF'nin metod adlarını kullanma.**

### 3.5 Channels (`apps.channels`)
Entegrasyon mecraları: Email, WhatsApp, Instagram, SMS, Webhook. `Channel` modeli
kimlik bilgilerini (API key, auth type) tutar. **Tasarım felsefesi:** Action'lar
yürütmeyi `channel_type`'a göre provider'a devreder; provider `Channel`'ın
credential'ını okur ve dış API çağrısını yapar.

### 3.6 Automations (`apps.automations`)
- **`Automation`** — konteyner. Tenant/branch'e ait. `allowed_roles` alanı taşır.
- **`AutomationTrigger`** — Scheduled / Manual / Webhook / Signal. Webhook ise güvenli
  URL + token üretir. `config` JSONField'ında tipe özel ayarlar.
- **`AutomationCondition`** — `eq/neq/gt/lt/contains/in`, aralarında AND/OR.
- **`AutomationAction`** — `order`'a göre çalışır, bir `Channel`'a bağlıdır,
  başarısızlık davranışı: stop / continue / retry.
- **`Template`** — dinamik içerik üretimi (SMS/e-posta gövdesi).
- **`AutomationLog`** — çalışma durumu ve context payload'ı.

### 3.7 Şema güdümlü validation — mimarinin belkemiği
Trigger ve action payload'ları birbirinden çok farklı (SMS `to`+`body` ister, webhook
`url`+`payload` ister). Bu yüzden config'ler `JSONField`'da tutulur ve **JSON Schema**
ile doğrulanır. Serializer'lar bu alanları statik olarak tanımlamaz; ilgili
entegrasyonun şema sözlüğüne karşı `config` payload'ını valide eder.

**Sonuç — yeni bir kanal eklerken (örn. Slack):**
1. `AutomationAction` modeline **dokunma**.
2. Yeni bir `ChannelType` ekle.
3. Bir Provider sınıfı yaz.
4. Beklenen `config` için bir validation şeması tanımla.

### 3.8 Çalışma zamanı akışı
```
Trigger ateşlenir
  -> trigger context oluşur
  -> Condition'lar değerlendirilir
  -> geçerse orchestrator Celery task'lerini kuyruğa atar
  -> Celery action'ları `order` sırasıyla alır
  -> Action ilgili Channel'ı çeker
  -> Channel Provider credential + action config ile çağrılır
  -> AutomationLog güncellenir
```

Uzun süren veya dış API çağrısı yapan **her şey** Celery task'inde kalmalı;
API sunucusunu bloklamak yasak.

---

## 4. Kod Stili

Bu proje bir öğrenme/geliştirme projesi ve stili kendine özgü. **Mevcut stile uy,
"düzeltmeye" kalkma** — tutarsız bir yamalama, tutarlı bir tuhaflıktan kötüdür.

- **Girinti: 8 boşluk.** (`core/roles.py` tab kullanıyor — dosya içi tutarlılığa uy,
  yeni dosyalarda 8 boşluk.)
- Virgülden sonra boşluk yok, `=` etrafında boşluk var: `def get_object(self,user,pk):`,
  `qs = self.model.objects.filter(tenant = user.tenant)`. Yaygın olan bu; PEP8'e
  çekmeye çalışma.
- Yorumlar İngilizce, açıklayıcı ve bazen uzun ("neden" anlatır). Bu iyi bir alışkanlık,
  sürdür.
- Kullanıcıya dönen tüm metinler `gettext_lazy as _` ile sarılır. Yerelleştirme
  `locale/` altında, `LANGUAGE_CODE = 'tr-tr'`.
- Serializer'lar app başına `serializers/` paketinde, alt tiplere bölünmüş
  (`serializers/actions/email.py`, `serializers/triggers/webhook.py`).
- `core` **dışarıdan hiçbir şey import etmez** (`core/roles.py` başındaki nota bak).
  Bu kural bilinçli: core, app'lere bağımlı olmamalı.

### Commit mesajı formatı
Repoda iki stil karışık. **Bundan sonra Conventional Commits kullan:**
```
feat(cache): add TTL to index sets
fix(core): resolve RBAC bypass in RoleBasedAccessMixin
refactor(automations): migrate trigger serializers to schema-driven validation
```
Gövdede madde madde ne + neden. Eski `(feat:channels)` stili terk edildi.

---

## 5. Geliştirme Ortamı ve Testler

```bash
./run_tests.sh                    # tüm suite (285 test) — venv'i gerekirse kendisi kurar
./run_tests.sh tests.test_core    # tek modül / sınıf / metot
./run_tests.sh --redis            # aynı suite, docker'da gerçek Redis'e karşı
```

Elle kurulum istersen:
```bash
python3 -m venv venv
./venv/bin/pip install -r requirements-dev.txt   # requirements.txt + fakeredis[lua]
./venv/bin/python manage.py migrate
./venv/bin/python manage.py test tests
```

- Varsayılan settings: `config.settings.development` (SQLite, `DEBUG=True`,
  tüm SQL sorguları console'a loglanır).
- **Suite hiçbir servise ihtiyaç duymaz** — Redis, Celery worker, Postgres yok.
  `fakeredis[lua]` Lua scriptlerini `lupa` ile gerçekten çalıştırır (mock değil).
  Gerçek sunucuya karşı: `./run_tests.sh --redis` veya elle
  `AUTOFLOW_TEST_REDIS_URL=redis://localhost:6399/0`. Verilen DB testler arası
  `FLUSHDB` edilir — tek kullanımlık bir instance ver.
- `.env` gerekli: `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DB_*`. `REDIS_URL`
  opsiyonel (default `redis://localhost:6379/0`).
- Uygulama **Redis kapalıyken de çalışır**: cache katmanındaki her hata `None`'a
  düşer ve istek DB'ye gider. `core.cache.service` logger'ında ERROR görürsün.

### Test suite haritası

| Modül | Kapsam | Test |
|---|---|---|
| `tests/factories.py` | Ortak fixture kurucuları — yeni test yazarken **buradan başla** | — |
| `tests/utils.py` | `CacheTestMixin` (fakeredis'li gerçek cache), `make_test_redis()` | — |
| `test_core.py` | roles, permissions, `BaseModel`, JWT claim'leri, pagination, exception handler | 35 |
| `test_channels.py` | provider registry, credential şema validasyonu, SMS/e-posta provider'ları, Channel CRUD + RBAC | 37 |
| `test_automations_engine.py` | evaluator (tüm operatörler + AND/OR), executor (sıra, stop/continue/retry), template_manager, action sınıfları, orchestrator, signal handler | 53 |
| `test_automations_serializers.py` | şema güdümlü trigger/action config validasyonu, webhook secret üretimi | 33 |
| `test_automations_api.py` | Automation CRUD + `allowed_roles`, Template CRUD, webhook auth, manuel tetikleme | 40 |
| `test_users_auth.py` | login/JWT, kayıt rol kuralları, admin update, silme, tenant/branch CRUD | 33 |
| `test_cache_lua.py` | **gerçek `.lua` dosyalarını çalıştırır** — Lua'ya dokunduysan bu yeşil kalmalı | 15 |
| `test_cache_service.py` | `CachingService` çağrı imzaları (mock Redis — Lua'yı doğrulamaz) | 8 |
| `test_cache_integration.py` | uçtan uca: gerçek view + signal + Lua, index zehirlenmesi ve invalidation | 15 |
| `test_api_endpoints.py` / `test_users_endpoints.py` | tenant ve kullanıcı uçları (mixin yolu) | 16 |

**Yeni test yazarken:** fixture'ları `tests/factories.py`'den al; endpoint testinde
cache'i mock'lama, `CacheTestMixin` kullan (mock'lamak BUG-02'yi aylarca gizledi).

---

## 6. Cache Katmanı — Sözleşme

Buraya dokunmadan önce `core/cache/__init__.py` başındaki docstring'i oku. Özet:

**İki tür key var ve farklı şeyler ifade ederler:**

| Tür | Örnek | Anlamı | Kim yazar |
|---|---|---|---|
| Data key | `users/<uuid>` | Tek bir serialize edilmiş nesne | detail + list yolu |
| Index set | `users/all`, `users/tenant:<uuid>` | **Bir kapsamın EKSİKSİZ liste cevabı** | sadece list yolu |

Bundan çıkan iki kural — ikisi de eski implementasyonda ihlal edilmişti:

1. **Sadece bir liste sorgusu index yazabilir, o da yalnızca kendi kapsamının
   index'ine.** Detail fetch kendini index'e eklemez; "tüm kullanıcılar" iddiasında
   bulunan tek üyeli bir set, cache'in hiç olmamasından kötüdür.
2. **Invalidation index'i SİLER, üyeyi SREM etmez.** Bir üyeyi çıkarmak, hâlâ
   "eksiksizim" diyen ama yanlış olan bir set bırakır — ve yeni oluşturulan bir satır
   için zaten hiçbir şey yapmaz.

Boş sonuç bilerek cache'lenmez: Redis boş set tutamaz, dolayısıyla "boş cevap" ile
"hiç cache'lenmemiş" ayırt edilemezdi. Boş listeler her seferinde DB'ye gider.

Kapsam (`scope`) tek kaynaktan gelir: `RoleBasedAccessMixin.get_user_scope(user)` hem
queryset filtresini hem cache index'ini belirler, böylece ikisi asla ayrışamaz.
`get_scope_index_key()` registry'de tanımlı olmayan bir kapsam için `None` döner —
yani key tutarsızlığı **yanlış cevaba değil, düz cache miss'e** dönüşür.

Trade-off, bilinçli: invalidation kabadır. Bir kullanıcıya dokunmak, o tenant ve
şubenin cache'li kullanıcı listesini düşürür. **Doğru-ve-soğuk, sıcak-ve-yanlıştan
iyidir.** Hit oranı yetmezse çözüm daha ince invalidation değil, kapsam bazlı
response cache'i düşünmektir.

---

## 7. Düzeltilmiş Bug'lar — Regresyon Uyarıları

Aşağıdakiler 2026-07-31'de bulundu ve **düzeltildi**; her biri için
`tests/test_cache_lua.py` veya `tests/test_cache_integration.py` içinde bir
regresyon testi var. Burada duruyorlar çünkü hepsi kolayca geri gelebilecek
sınıfta hatalar. Kök neden analizleri `logbook.md` → 2026-07-31.

### Tekrar etmemesi gereken kalıplar

| Kalıp | Ne olmuştu | Kural |
|---|---|---|
| `dict.get(k, default=0)` | `TypeError: dict.get() takes no keyword arguments` — tüm `/api/v1/users/` uçları 500 | `dict.get` fallback'ini **pozisyonel** ver |
| `CELERY_BORKER_URL`, `'django_db'` | Yazım hatası hiç hata vermez, sessizce varsayılana düşer; `.delay()` çağıran her uç 500 | Celery ayar adlarını kopyala-yapıştır, gözle doğrula. Backend `'django-db'` (tire) |
| Sender'sız `@receiver(pre_save)` | Projedeki *her* model kaydında çalışır; registry'de olmayan modelde `AttributeError` → zamanlanmış otomasyon oluşturulamaz | Global receiver bağlarsan **bilinmeyen her modeli tolere et** |
| `instance.tenant_id` | `Tenant`'ta böyle alan yok → Tenant güncellemek çöker | Kapsam için daima `instance.get_scope_ids()` (BaseModel; Tenant/Branch override eder) |
| `if not instance.pk:` ile create tespiti | `BaseModel.id` `default=uuid4` — pk INSERT'ten önce de dolu, guard **ölü kod** | Create tespiti: `instance._state.adding` veya `post_save`'in `created` bayrağı |
| `ready()` içinde DB sorgusu | Temiz veritabanında `migrate` bile çalışmıyordu | Boot'ta sorgu atma; `post_migrate` + `DatabaseError` yakala |
| `user.is_staff` ile "şube kapsamlı kullanıcı" demek | `is_staff` Django admin bayrağı; staff **rolü** için `False`, super_admin için `True` | Rol için `user.is_staff_role`, admin paneli için `is_staff` |
| Celery task'ine UUID geçmek | `CELERY_TASK_SERIALIZER='json'` → `Object of type UUID is not JSON serializable` | `.delay()`'e her zaman `str(pk)`; payload'ı DRF `JSONEncoder`'dan geçir |
| `automation.automationcondition_set` | Model `related_name='conditions'` tanımlıyor, default accessor **yok** → motor her çalıştırmada `AttributeError` | İlişki erişiminden önce `related_name`'e bak |
| `logger.error(msg, str(e))` | İkinci argüman `%`-format parametresi sanılır, mesajda placeholder yok → logging'in kendisi patlar ve hatayı yutar | İstisna loglarken `logger.exception(msg)` |
| Mock'lanmış Redis ile Lua test etmek | 5 test yeşilken `single_set.lua` hiçbir zaman index yazmadı | Lua değişikliği = `tests/test_cache_lua.py` (fakeredis, gerçek yürütme) |
| Cache'i mock'layarak endpoint testi | Bozuk `CELERY_RESULT_BACKEND`'i gizledi | Endpoint testlerinde `tests.utils.CacheTestMixin` kullan, cache'i stub'lama |
| İstisnayı loglamadan 500 döndürmek | Yukarıdakilerin çoğu aylarca görünmez kaldı | `core/exceptions.py` artık `logger.exception` çağırıyor — kaldırma |
| Ayar anahtarında yazım hatası (`DEFAULT_PERMISSON_CLASSES`) | DRF bilinmeyen anahtarı yok sayar → varsayılan sessizce `AllowAny` kalır, tüm otomasyon uçları anonime açıktı | Ayar adını değiştirdiysen bir anonim istek testiyle doğrula |
| `serializer_cls(data=config).is_valid()` | `BaseConfigSerializer`'ın alanı yok → `validate_config` **hiç çağrılmaz**, `validated_data` boş döner ve config sessizce silinir | Şema doğrulaması için `serializer_cls().validate_config(cfg)` — channels'taki `validate_credentials` ile aynı desen |
| Validator'ın `False` döndürmesi | Şema yürüyücüsü **sadece istisna yakalar**; `croniter.is_valid` False dönüyordu, her cron ifadesi kabul ediliyordu | Validator reddediyorsa **raise etmeli** (`serializers/validators.py` başındaki nota bak) |
| `read_only_fields = '__all__'` | DRF liste/tuple ister → `TypeError` | `read_only_fields = fields` |
| `check_tenant_access(user, tenant_obj)` | Serializer'lar model instance geçer; `str(Tenant)` **adı** verir, UUID ile asla eşleşmez | Artık `_as_id()` normalleştiriyor — ama id geçmek yine de daha net |
| Nested serializer çıktısını id sanmak | `AutomationReadSerializer.tenant` bir **dict**; cache'li detail yolunda 403 üretiyordu | `core/mixins._nested_id()` çözüyor; yeni nested alan eklersen kontrol et |
| `provider_class = EmailProvider()` | `base.execute` `self.provider_class()` çağırıyor → instance çağrılmaya çalışılır | Registry'lerde **sınıf** tut, instance değil |

### Bilerek bırakılanlar

- `AutomationsConfig.ready()` hâlâ Django'nun "DB access during app initialization"
  uyarısını veriyor. Sinyal tetikleyicilerinin bir Celery worker'da da bağlanması
  gerektiği için `request_started`'a ertelenemiyor. Uyarı kozmetik; temiz DB'de
  `migrate` artık çalışıyor.
- `apps/users/views.py` `RoleBasedAccessMixin`'i kullanmıyor; registry `User`'ı
  `CACHED: True` işaretlese de kullanıcı uçları cache'e uğramıyor. Kasıt mı unutulmuş
  mu belirsiz — mixin'e taşımadan önce karar ver.
- `/api/v1/users/me/` PUT yalnızca **şifre değiştirir**. Kullanıcının kendi adını
  güncelleyebileceği bir uç yok.
- `apps/websites` ve `apps/analytics` boş iskeletler (model/view yok, `urlpatterns = []`).
  Testleri de yok — test edilecek davranış yok.
- Swagger/schema uçları (`/api/schema/`, `/api/docs/`) artık kimlik doğrulaması
  istiyor, çünkü DRF varsayılanı düzeltildi. Public istiyorsan view'lara açıkça
  `permission_classes = [AllowAny]` ver.
- `AutomationDetailView` `allowed_roles_for_list` tanımlamıyor; erişim `allowed_roles`
  alanı üzerinden obje düzeyinde denetleniyor. Kasıtlı.

---

## 8. Bu Projede Çalışırken

1. **Yetkilendirmeyi önce oku** — API view'ı yazmadan önce `core/permissions.py` ve
   `core/mixins.py`. Mixin'de DRF metod adlarını gölgeleme (`check_permissions`
   bir dönem tüm `permission_classes`'ı devre dışı bırakmıştı).
2. **Yeni kanal = yeni provider + yeni şema**, model değişikliği değil (§3.7).
3. **Cache'e dokunuyorsan §6'daki sözleşmeyi bozma.** Yeni bir model cache'lenecekse
   registry'ye ekle ve `idx_keys`'in hepsinin `cache_key` ile başladığından emin ol —
   `tests/test_cache_lua.py` bunu zaten kontrol ediyor.
4. **Signal/Celery'e dokunmadan önce** `core/signals.py` ve
   `apps/automations/signals.py`'yi oku.
5. **Testleri çalıştır: `./run_tests.sh`** (285 test, hiçbir servis gerekmez).
   Cache/Lua'ya dokunduysan `./run_tests.sh --redis` ile gerçek Redis'e karşı da koştur.
6. **Yeni davranış = yeni test.** Fixture'lar `tests/factories.py`'de; §5'teki
   haritadan hangi dosyaya ait olduğunu bul.
7. **Her anlamlı değişikliği `logbook.md`'ye zaman damgasıyla yaz**: ne, neden, nasıl;
   hata ise kök neden + denenip başarısız olan yollar.
