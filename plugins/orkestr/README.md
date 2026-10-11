# Orkestr — Claude Code plugin (Phase 1 MVP)

> **Ad hakkında not:** `orkestr` yalnızca teknik plugin kimliğidir. Ürün adının Orkestr mi Orkestra mı olacağı henüz kararlaştırılmadı. Yeniden adlandırma adımları aşağıda.

Orkestr, bir yazılım projesini küçük bir "yapay zekâ şirketi" gibi yönetmenize yardım eder. Planlama yapar, sınırlı ve yapılandırılmış toplantılar düzenler, karar kayıtları tutar. Görevleri onay kapılarıyla geliştirir, kanıta dayalı inceleme yapar, durum raporu ve retrospektif hazırlar.

Tüm proje durumu, yönettiğiniz projenin kökündeki `.orkestr/` klasöründe yerel dosyalar olarak tutulur.

## Gereksinimler

| Gereksinim | Not |
| :- | :- |
| Plugin destekli Claude Code | **Test edilen sürüm: 2.1.295**, masaüstü uygulamasına gömülü olan |
| Node.js 20+ | Test edilen: v20.11.1. Durum aracı ve hook yalnızca Node standart kütüphanesini kullanır; `npm install` gerekmez |
| Windows, macOS veya Linux | Geliştirme ve testler Windows 11 üzerinde yapıldı |

> **Dikkat:** Bu makinede `PATH` üzerindeki `claude` komutu eski bir npm kurulumu (**1.0.51**) ve plugin desteği yok; `claude plugin ...` komutları onunla çalışmaz. Komut satırından plugin işlemleri için masaüstü uygulamasının gömülü sürümünü kullanın:
>
> ```
> "%APPDATA%\Claude\claude-code\2.1.295\<hash>\claude.exe" plugin validate plugins\orkestr
> ```
>
> Ya da eski npm sürümünü kendiniz güncelleyin (`npm i -g @anthropic-ai/claude-code`). Bu, ağdan paket indirir; kararı size bırakıldı.

## Maliyet uyarısı (önemli)

Plugin hiçbir ücretli servis, API, MCP sunucusu veya paket eklemez ve betikleri ağa bağlanmaz. Bu kurallar otomatik testlerle denetlenir.

**Buna rağmen sıfır maliyet garanti edilemez.** Model kullanımı, Claude Code'u hangi hesapla kullandığınıza bağlıdır:

- **Abonelik planı (Pro/Max vb.):** kullanım plan limitlerinizden düşer.
- **API anahtarıyla (kullandıkça öde):** her ajan çağrısı ücretlendirilir.

Hangi faturalandırma biçimini kullandığınız bu kurulumda doğrulanamadı. Komut satırı aracı `authMethod: none` döndürdü; masaüstü uygulaması kendi oturumunu kullanıyor.

Kullanımı azaltmak için:
- Tüm komutlar yalnızca siz çağırdığınızda çalışır (`disable-model-invocation: true`).
- Her iş akışının ajan çağrı bütçesi sınırlıdır.
- Ajanlar başka ajan başlatamaz.

## Kurulum

### A) Geçici olarak denemek (önerilen ilk adım; ayarlarınızı değiştirmez)

Yönetmek istediğiniz projenin klasöründe Claude Code'u plugin dizini ile başlatın:

```bash
claude --plugin-dir "C:/Users/alper/OneDrive/Masaüstü/orkestr/plugins/orkestr"
```

Plugin yalnızca o oturum için yüklenir.

### B) Kalıcı kurulum (GitHub üzerinden)

Bu adımlar Claude Code kullanıcı ayarlarınızı değiştirir. Claude Code içinde:

```
/plugin marketplace add alperglcoglu/Orkestr
/plugin install orkestr@orkestr
```

### C) Kalıcı kurulum (yerel marketplace üzerinden)

Claude Code içinde:

```
/plugin marketplace add C:/Users/alper/OneDrive/Masaüstü/orkestr
/plugin install orkestr@orkestr
```

Ya da komut satırından, plugin destekli `claude` ile:

```bash
claude plugin marketplace add "C:/Users/alper/OneDrive/Masaüstü/orkestr"
```

```bash
claude plugin install orkestr@orkestr
```

Kaldırmak için:

```bash
claude plugin uninstall orkestr
```

Değişikliklerden sonra açık bir oturumda `/reload-plugins` çalıştırın.

### Kurulumu doğrulama

```bash
claude plugin validate plugins/orkestr --strict
```

```bash
claude --plugin-dir plugins/orkestr plugin details orkestr
```

İkinci komut 7 skill, 10 ajan ve 1 hook göstermelidir.

## Komutlar

| Komut | Ne yapar | Ajan bütçesi |
| :- | :- | :- |
| `/orkestr:run [hedef]` | **Ana komut.** Hedeften tamamlanma raporuna kadar her şeyi kendisi yürütür: gereksinim, plan, görevler, uygulama, test, inceleme, düzeltme, gerekirse toplantı. Size yalnızca aşağıdaki "Size ne zaman danışılır?" durumlarında gelir | ≤ 30 / çalıştırma |
| `/orkestr:plan [hedef]` | Projeyi başlatır (gerekirse), PRD ve görev planı çıkarır, görevleri `.orkestr/` içine kaydeder. `bounded` modda plan hemen dahili çalışma planı olur; `supervised` modda onayınızı ister | ≤ 2 |
| `/orkestr:meeting <soru>` | 2–4 ilgili ajanla, en fazla 2 turluk toplantı yapar; kararı, karşı görüşleri ve aksiyonları kaydeder; aksiyonlar göreve dönüşür | ≤ katılımcı × tur (≤ 8) |
| `/orkestr:build [T-id]` | Tek bir yetkili görevi uygular; gerçek testleri kanıt olarak kaydeder; **en fazla 3 düzeltme denemesi** yapar; kesintiye uğramış işi kaldığı yerden sürdürür | ≤ 4 |
| `/orkestr:review [T-id]` | QA ve gerekirse Security incelemesi yapar; doğrulanmış bulguları, şüpheli bulgulardan ve varsayımlardan ayırır; kanıt yeterliyse görevi tamamlar | ≤ 2 |
| `/orkestr:status [pause\|resume\|cancel]` | Kayıtlardan durum raporu verir: engeller, başarısız görevler, bekleyen onaylar, sizi bekleyen kararlar. Projeyi durdurur, sürdürür veya iptal eder | 0 |
| `/orkestr:retro [kapsam]` | Kanıta dayalı retrospektif hazırlar; dersleri kaydeder; süreç değişikliklerini yalnızca **önerir** | 0 |

Uzman ajanlar şunlardır:
- `orkestr:product-manager`: gereksinimler ve kabul kriterleri
- `orkestr:system-architect`: sistem tasarımı (bileşenler, veri akışı, veri modeli, arayüz sözleşmeleri, ölçeklenebilirlik ve güvenilirlik, dizin düzeni)
- `orkestr:tech-lead`: görevler, bağımlılıklar ve paralel çalışma için dosya kapsamları
- `orkestr:ml-engineer`: yapay zekâ ve makine öğrenmesi bileşenleri. Yalnızca gerçekten gerektiğinde kullanılır; önce ML'siz veya ücretsiz yerel çözümler değerlendirilir. Ücretli model API'si eklemez, büyük model indirmeden önce onay ister, ölçülmemiş metrik raporlamaz.
- `orkestr:data-engineer`: veri modeli, şemalar, geri alınabilir migration'lar, veri hatları, analiz. Tablo silme gibi yıkıcı veri işlemleri onaya bağlıdır.
- `orkestr:backend-engineer`
- `orkestr:frontend-engineer`
- `orkestr:qa-engineer`
- `orkestr:security-reviewer`
- `orkestr:researcher`

CEO ve Proje Yöneticisi rolleri ayrı ajan değildir; skill'leri çalıştıran ana oturum (orkestratör) bu rolleri üstlenir.

## Tipik akış (otonom)

```
/orkestr:run Basit bir yapılacaklar listesi uygulaması; ücretsiz araçlar; testli
```

Orkestr şu adımları kendisi yürütür:

1. **Product Manager** gereksinimleri yazar.
2. **Tech Lead** mimariyi ve bağımlı görevleri çıkarır.
3. Plan hemen dahili çalışma planı olur; onay beklenmez.
4. Her görev için:
   - geliştirici ajan uygular;
   - testler **gerçekten** çalıştırılır;
   - QA (ve gerekirse Security) inceler;
   - bulgular göreve not olarak yazılır ve geliştiriciye geri döner.
5. Test başarısız olursa izin istenmez; en fazla 3 düzeltme denemesi yapılır.
6. Hâlâ çözülmezse ilgili ajanlarla otomatik bir **kurtarma toplantısı** yapılır. Karar çıkarsa iş kaldığı yerden devam eder.
7. Teknik varsayımlar kaydedilir; size soru sorulmaz.
8. Sonunda kısa bir **tamamlanma raporu** (`ork report`) verilir:
   - neler yapıldı;
   - hangi testler gerçekten çalıştı ve sonuçları;
   - hangi kararlar varsayıma dayanıyor;
   - çözülemeyenler;
   - sizden beklenenler.

Oturum kesilirse `/orkestr:run` komutunu yeniden çalıştırın; kayıtlardan devam eder. Diğer komutlar adımları tek tek elle yürütmek içindir.

### Paralel çalışma

Birbirinden bağımsız görevler aynı anda geliştirilir:

- Tech Lead her göreve bir **dosya kapsamı** (`scope`, örn. `src/api/**`) verir.
- Kapsamları çakışmayan görevler aynı **dalgada** paralel yürütülür; varsayılan sınır aynı anda 2 görev.
- Kapsamı olmayan bir görev tek başına çalışır.
- `package.json` gibi ortak dosyalar tek bir görevin kapsamındadır; diğer görevler o göreve bağımlı olur.
- Ajanlar bitince her görevin değiştirdiği dosyalar `ork scope check` ile kapsamına göre kontrol edilir. Kapsam dışı değişiklik başarısız kontrol sayılır ve düzeltilir.
- Testler, dalga yazmayı bitirdikten sonra görev görev sırayla çalışır; inceleme paralel yapılabilir.
- Durum aracı, çakışan veya sınırı aşan bir başlatmayı reddeder.

### Size ne zaman danışılır?

Yalnızca şu durumlarda:
- gereksinimlerde makul bir varsayımla çözülemeyen bir çelişki;
- ürün hedefi, temel kullanıcı deneyimi veya önemli kapsam kararı değişecekse;
- geri alınamaz veya veri kaybı riski taşıyan bir işlem;
- harici veya ücretli servis ya da ek maliyet;
- gerçek kimlik bilgileri, hassas veri veya yetki yükseltme;
- açık yetkilendirme olmadan `git push`, üretime dağıtım veya herkese açık yayın;
- düzeltme sınırları ve kurtarma toplantısı tükenmişse.

### Özerklik modu

| Mod | Davranış |
| :- | :- |
| `bounded` (yeni projelerde varsayılan) | Yukarıdaki otonom davranış |
| `supervised` | Plan ve toplantı kararları onayınızı bekler. `autonomy` alanı olmayan eski projeler bu modda sayılır |

Modu yalnızca siz değiştirebilirsiniz; hook bu komutta da onay sorar:

```bash
node plugins/orkestr/scripts/orkestr.mjs project autonomy supervised --by founder
```

## Onaylar ve güvenlik

Aşağıdaki işlemler **sizin açık onayınız** olmadan yapılmaz:
- `git push`;
- üretime dağıtım;
- herkese açık yayın;
- dış iletişim;
- harcama;
- izin değişikliği;
- yetki dışındaki hassas verilere erişim;
- yıkıcı silme işlemleri.

Onaylar `A-001` gibi kayıtlarla tutulur. Reddedilen bir onay asla onaya dönüşmez.

"Kurucu işlemleri" kaydınızı oluşturur ve yalnızca sohbette "evet" dedikten sonra çalıştırılır:
- plan onayı ve karar onayı (`supervised` modda veya `founder_required` kararlarda);
- görev yetkilendirme;
- yeniden deneme;
- özerklik modu değişikliği;
- proje iptali.

`bounded` modda rutin plan ve kararlar bu komutlara gerek duymaz.

Koruma birkaç katmanlıdır; hiçbiri tek başına güvenlik sınırı değildir:

1. Claude Code'un kendi izin sistemi.
2. Durum aracı: onaysız görev başlatmayı, kanıtsız tamamlamayı, sınır aşımını ve otomatik yeniden denemeyi reddeder.
3. `PreToolUse` hook'u (`scripts/guard.mjs`): yukarıdaki riskli komutlarda ve `.orkestr/` durum dosyalarının elle düzenlenmesinde onay sorar (`ask`).

> **Hook hakkında:** Hook, plugin etkinken **tüm oturumlarınızda** çalışır. Örneğin `git push` veya `rm -rf` kullandığınızda, Orkestr dışındaki işlerde de onay sorar. Hook komut metnine bakar; alışılmadık yazımları kaçırabilir. `bypassPermissions` modunda `ask` kararının hâlâ soru sorup sormadığı **doğrulanmadı**.

Kimlik bilgileri, tokenlar ve parolalar loglara ve kayıtlara yazılmaz. Durum aracı yaygın sır kalıplarını yazmadan önce `[REDACTED]` ile maskeler, `validate` komutu da şüpheli değerleri raporlar. Bu bir güvenlik ağıdır, garanti değildir.

## Sınırlar

| Sınır | Değer | Uygulayan |
| :- | :- | :- |
| Toplantı katılımcısı | en fazla 4 | durum aracı |
| Toplantı tartışma turu | en fazla 2 | durum aracı |
| Build/fix düzeltme denemesi | en fazla 3; sonraki başarısız kontrol görevi `FAILED` yapar | durum aracı |
| Ajan tur sayısı | ajan başına `maxTurns` (12–30) | Claude Code |
| İç içe ajan çağrısı | yok (ajanlarda `Agent` aracı yok) | Claude Code |
| Paralel uygulama görevi | aynı anda en fazla 2, yalnızca çakışmayan dosya kapsamlarıyla | durum aracı |
| Kurtarma toplantısı | görev başına 1 (`bounded`); karar çıkarsa görev yeniden açılır ve 3 deneme hakkı tazelenir | durum aracı |
| Bunun ötesinde yeniden deneme | yalnızca sizin `--retry` onayınızla | durum aracı ve hook |
| `/orkestr:run` başına ajan çağrısı | ≤ 30 (talimatla; makineyle değil) | skill |

## Proje verileri (`.orkestr/`)

```text
.orkestr/
├── project.json      proje kaydı, sınırlar, kimlik sayaçları
├── tasks.json        görevler (T-001 …)
├── approvals.json    onaylar (A-001 …)
├── history.jsonl     yalnızca eklenen yürütme geçmişi
├── requirements/     prd.md
├── architecture/     architecture.md (isteğe bağlı)
├── meetings/         M-001.json + M-001.md
├── decisions/        D-001.json + D-001.md
├── reviews/          R-001.json + R-001.md
├── retros/           RT-001.json + RT-001.md
├── memory/           lessons-learned.md
└── drafts/           orkestratörün yazdığı geçici giriş dosyaları
```

- JSON dosyaları asıl kaynaktır. Yanlarındaki `.md` dosyaları otomatik üretilir; elle düzenlemeyin.
- Durum yalnızca durum aracıyla değişir. Aracı doğrudan da kullanabilirsiniz:

  ```bash
  node plugins/orkestr/scripts/orkestr.mjs help
  ```

- Git kullanıyorsanız `.orkestr/drafts/` klasörünü `.gitignore` dosyasına eklemek isteyebilirsiniz.

### Görev durumları

`BACKLOG → READY → IN_PROGRESS → REVIEW → COMPLETED`; ayrıca `BLOCKED`, `FAILED` ve `CANCELLED`.

- Görev, yetkilendirilip bağımlılıkları tamamlanınca otomatik olarak `READY` olur.
- Bir görev `FAILED` veya `CANCELLED` olursa, ona bağlı görevler otomatik olarak `BLOCKED` olur.

### Durdurma, sürdürme, iptal ve kurtarma

- `/orkestr:status pause <neden>`: yeni görev başlatılamaz.
- `/orkestr:status resume`: proje kaldığı yerden devam eder.
- `/orkestr:status cancel <neden>`: geri alınamaz; açık görevler ve bekleyen onaylar iptal edilir. Önce açık onayınız istenir.
- Yarıda kalan iş: `IN_PROGRESS` durumunda kalan görev durum raporunda görünür. `/orkestr:build T-xxx` onu kanıtlarından ve çalışma ağacından devam ettirir; işi sıfırdan başlatmaz.
- Dosyalar atomik olarak yazılır ve aynı anda iki yazıcıyı bir kilit dosyası engeller. Bozuk veya tutarsız durum `validate` ile raporlanır ve iş akışları durur; otomatik onarım yapılmaz.

## Testler

Depo kökünden çalıştırın:

```bash
node --test plugins/orkestr/tests/
```

Son çalıştırma: **70 test, 70 başarılı** (Windows 11, Node 20.11.1). Otonom davranış testleri `tests/autonomy.test.mjs`, paralel çalışma testleri `tests/parallel.test.mjs` dosyasındadır.

Kapsam:
- proje başlatma ve üzerine yazmayı reddetme;
- plan içe aktarma ve döngüsel bağımlılık reddi;
- yetkilendirme;
- tüm durum geçişi kuralları;
- bağımlılık engelleme ve engel kaldırma;
- 3 düzeltme denemesi sınırı;
- yeniden deneme kapısı;
- tamamlama kanıtı kuralları;
- toplantı sınırları;
- toplantı → karar → görev zinciri;
- karar reddi;
- onay kapıları;
- durdurma, sürdürme ve iptal;
- kilit ve geçici dosya kurtarma;
- sır maskeleme;
- durum raporu içeriği;
- hook kararları;
- plugin yapısı;
- bütçe kuralları;
- şablonların geçerliliği.

## Yeniden adlandırma (Orkestr → başka bir ad)

1. `plugins/orkestr/.claude-plugin/plugin.json` içindeki `name` alanını değiştirin. Gerekirse klasör adını ve `.claude-plugin/marketplace.json` dosyasını da güncelleyin.
2. Skill, ajan ve referans dosyalarındaki `orkestr:` önekini yeni adla değiştirin. `tests/plugin.test.mjs` içindeki ad kontrol testi, eksik kalan referansları yakalar.
3. Veri klasörü adı yalnızca `scripts/lib/core.mjs` dosyasındaki `DATA_DIR_NAME` sabitinde tanımlıdır.
4. Testleri ve `claude plugin validate` komutunu yeniden çalıştırın.

## Bilinen sınırlamalar

- **Canlı model testi yapılmadı.** Skill'lerin bir Claude oturumunda baştan sona izlendiği uçtan uca senaryo bu ortamda çalıştırılmadı (komut satırı aracında oturum yok, ayrıca kullanım maliyeti doğabilir). Durum aracı, hook ve plugin yüklemesi otomatik olarak doğrulandı; skill talimatlarının model tarafından doğru uygulanması **manuel doğrulama gerektirir**.
- Skill'lerdeki ajan çağrı bütçeleri talimatla sınırlanır, makineyle uygulanmaz. Toplantı ve düzeltme sınırları ise durum aracıyla uygulanır.
- Kurucu onayı, `--by founder` bayrağı ve hook'un onay sorusuyla korunur. Durum aracı, komutu gerçekten bir insanın onayladığını kendi başına doğrulayamaz.
- Hook komut metnine bakar; alışılmadık komut biçimlerini kaçırabilir.
- Sır maskeleme kalıp tabanlıdır; her sırrı yakalayamaz.
- Paralel çalışmada ajanların kendi kapsamı dışına yazmaması **talimatla** sağlanır. `ork scope check` ihlali sonradan yakalar ama yazmayı engellemez. Git yoksa değişen dosya listesi ajanın raporuna dayanır. Görev başına ayrı git worktree ile tam izolasyon sonraki faza bırakıldı.
- Kapsam çakışması tutucu biçimde hesaplanır; bazen gerçekte çakışmayan iki görevi de sıraya koyabilir (güvenli taraf).
- `/orkestr:run` döngüsünün bir Claude oturumunda baştan sona doğru izlendiği **canlı olarak test edilmedi**. Durum aracının otonom kuralları (otomatik yetkilendirme, otomatik karar onayı, kurtarma toplantısı, bütçe sınırları) otomatik testlerle doğrulandı.
- `founder_required` işaretinin doğru konması orkestratörün yargısına bağlıdır; araç, hangi kararın ürün düzeyinde olduğunu kendi başına anlayamaz.
