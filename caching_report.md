# Caching Service (WIP) Analiz Raporu

## 1. Mimari ve Temel Mantık
* **Redis & Lua:** Caching işlemleri (set, get, bulk_get, invalidate) Redis üzerinde Lua scriptleri çalıştırılarak (`evalsha`) atomik ve hızlı bir şekilde yapılıyor.
* **Registry Yapısı:** `core/cache/__init__.py` içinde hangi modellerin cache'leneceği (`CACHED: True/False`), TTL süreleri ve Index key'leri (`idx_keys` örneğin `users/tenant:{tenant_id}`) tanımlanmış. 
* **RoleBasedAccessMixin Entegrasyonu:** Mixin, veritabanı sorgularını aradan çıkarıp (intercept) Redis'ten veriyi çekmeye çalışıyor. Bulamazsa DB'den alıp (`get_qs_db`, `get_obj_db`), Celery task'leri (`single_cache`, `bulk_cache`) aracılığıyla asenkron olarak cache'e yazıyor.
* **Signal Bazlı Invalidation:** Model kaydedildiğinde veya silindiğinde (`pre_save`, `post_delete`), Redis'teki ilgili key'ler ve index'ler temizleniyor. Alt modeller (Trigger, Action vb.) parent modele (Automation) ait cache'i düşürüyor.

---

## 2. Eksikler, Hatalar ve "Nerede Patlayabilir?"

### A. `get_object()` ve `get_queryset()` Dönüş Tipleri (Kritik Hata)
Şu anki `RoleBasedAccessMixin` tasarımı, `get_object` ve `get_queryset` fonksiyonlarında DB objesi (Model Instance) veya QuerySet dönmek yerine, **JSON serialize edilmiş string'i dict'e çevirip** dönüyor (`return json.loads(obj)`).
* **Nerede Patlar?** 
  * DRF view'larındaki `put` ve `delete` metotları, dönen objenin bir Django Model instance'ı olmasını bekler. Örneğin `AutomationDetailView` içindeki `delete` metodunda `automation.delete()` çağrılıyor. `automation` bir dict olduğu için `AttributeError: 'dict' object has no attribute 'delete'` hatası verecektir.
  * DRF Serializer'lara instance olarak dict gönderilirse davranış değişebilir.

### B. Parent Model Çözümleme Hatası (Signals)
`core/signals.py` dosyasında, parent modelin cache'ini düşürmek için şu satır kullanılmış:
`parent = apps.get_model('core', parent_model_name).objects.get(id=parent_pk)`
* **Nerede Patlar?**
  Hardcoded olarak `'core'` app'ine bakıyor. Ancak parent model örneğin `Automation` ise, bu model `automations` app'inin altındadır. `LookupError: App 'core' doesn't have a 'Automation' model` hatası fırlatır.

### C. Lua Script Pipeline Kullanımı
`bulk_set` fonksiyonunda `pipe = self.r.pipeline()` kullanılıyor. Tekil bir Redis sunucusunda sorun olmaz, ancak eğer ileride Redis Cluster yapısına geçilirse, Lua scriptleri pipeline içinde farklı node'lara dağılacağı için `NoScriptError` veya cross-slot hataları potansiyeli yaratır (Şu anlık göz ardı edilebilir ama akılda tutulmalı).

### D. `caching.py` vs `service.py`
`core/caching.py` bir taslak (roadmap) olarak kalmış ve bitirilmemiş. Asıl servis `core/cache/service.py` içinde yazılmış. Proje temizliği için `core/caching.py` dosyasına ihtiyaç olup olmadığına karar verilmeli.

---

## 3. Uygulama Boyunca Uygulanabilir Olması İçin Neler Yapılmalı?

### Çözüm 1: Cache Katmanının Yerini Değiştirmek (Önerilen)
Cache kontrolünü `get_object` ve `get_queryset` gibi ORM seviyesi metotların içinde yapmak yerine, **View seviyesinde (Response bazlı)** veya **Manager seviyesinde** yapmalıyız. 
* Eğer View response'unu (Serializer çıktısını) önbelleğe alırsak (`list` ve `retrieve` metotlarını ezerek), `get_object` ve `get_queryset` orijinal halinde (QuerySet dönerek) kalır. Böylece `put`, `delete` ve yetki kontrolleri sorunsuz çalışmaya devam eder.

### Çözüm 2: Dict Dönüşünü Modele Çevirmek veya ID ile İşlem Yapmak
Eğer mevcut yapı (Mixin üzerinden) korunacaksa:
1. `put` ve `delete` view metotları `obj.delete()` yerine `Model.objects.filter(id=obj['id']).delete()` şeklinde yazılmalı.
2. Yetki kontrolleri `obj.tenant` gibi property erişimleri yerine `obj.get('tenant')` ile yapılmalı (zaten `# WIP` olarak işaretlenmiş kısım).

### Sinyal Optimizasyonu
`REGISTRY` içindeki parent tanımı sadece model adını değil, app adını da içermeli:
`'parent': ('automations.Automation', 'automation_id')`
Ve `signals.py` içerisinde model bu app tanımına göre dinamik çağrılmalı (Örn: `apps.get_model('automations', 'Automation')`).

---

**Özet:** Caching servisinin Lua destekli asenkron yazma altyapısı çok sağlam tasarlanmış. Ancak, Django ORM nesneleri ile Serialize edilmiş JSON dict'leri birbirine karışmış durumda. Bu "Tip (Type) uyuşmazlığı", mevcut haliyle tüm uygulamanın CRUD işlemlerini kıracaktır. İlk aksiyon olarak Mixin'deki cache interception katmanını revize etmemiz gerekiyor.
