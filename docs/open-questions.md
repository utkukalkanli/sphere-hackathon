# Açık sorular

Son güncelleme: 2026-11-12. Kaynak: `Sphere Forge Kickoff.pdf` (12 slayt), README ve `scripts/eval.py`.
Cevap geldikçe ilgili maddenin altına **Cevap:** satırı ekle.

## A. Organizatöre sorulacaklar

### Skorlayıcı ve pratik cevaplar

1. Slayt 6 "skorlayıcı pratik setle birlikte geliyor, yerel skor leaderboard ile eşleşir" diyor. Skorlayıcı
   kodu nerede? Template repoda (`sphere-energy/hackathon-submission`) ve bizim repoda yok.
2. Slayt 5 "9 pratik parça cevaplarıyla yayınlandı" diyor. Pratik parçaların gerçek STEP dosyaları nerede?
3. Leaderboard parça başına ve terim başına (hacim / yüzey / boyut / delik) skor gösteriyor mu, yoksa yalnızca
   toplam mı?

Skorlayıcı kodu verilirse 4–9 gereksiz. Verilmezse, `scripts/eval.py`'deki varsayımlarımız şunlar:

4. **Hacim (%30):** biz hacim IoU'su varsaydık (kesişim / birleşim). Doğru mu, yoksa başka bir oran mı
   (ör. kesişim / gerçek hacim, Dice)? Tam boolean ile mi, voksel/nokta örneklemesiyle mi hesaplanıyor?
5. **Yüzey (%30):** biz iki yüzeyden örneklenen noktaların F-skorunu (kesinlik + geri çağırma), 0.5 / 1 / 2 / 5 /
   10 % eşiklerinin düz ortalaması olarak varsaydık. Gerçek eşikler hangileri, nasıl ağırlıklandırılıyor?
   Ölçüm tek yönlü mü, çift yönlü mü?
6. **"Parça boyutu":** eşikler neyin yüzdesi? Biz sınırlayıcı kutunun köşegenini varsaydık (en uzun kenar da
   olabilir).
7. **Boyut (%20):** biz üç kutu kenarını ayrı ayrı (büyükten küçüğe eşleyip) puanlayıp ortalamasını aldık;
   eğri (0 %, 1) – (2 %, 0.9) – (20 %, 0) noktalarından geçen kırık doğru. Gerçek eğri ne? Üç kenarın ortalaması
   mı, en kötüsü mü? Kutu hizalamadan sonra mı ölçülüyor?
8. **Delik (%20):** biz `1 − |bizim − gerçek| / max(gerçek, 1)` varsaydık, yani fazla delik de eksik delik kadar
   ceza alıyor. Doğru mu? Hiç deliği olmayan parçada bu terim tam puan mı? Sayım yalnızca topolojik cins
   (genus) mi, yoksa deliklerin konumu/çapı da eşleştiriliyor mu?
9. **Hizalama:** hangi yöntem (ana eksenler, ICP, 24 eksen döndürmesinin denenmesi)? Çıktıyı zaten girdinin
   X/Y/Z çerçevesinde veriyoruz; bu durumda hizalama skoru bozabilir mi?

### Kurallar

10. **Dosya adı:** slayt 4 `outputs/<id>.step`, README "kesinlikle `test_case_<n>.stp`" diyor. Hangisi?
11. **Süre:** slayt 6 "dokuz parça için 1 saat", README "20 dakika, sonra öldürülür" diyor. Kod donduktan
    sonraki çalıştırmada hangisi geçerli?
12. **Donanım / model:** yeniden çalıştırmada model bizim Brev makinemizdeki uç noktadan mı servis edilecek
    (README böyle diyor), yoksa organizatörün RTX PRO 6000'inde mi? Harici API modeli (kendi anahtarımızla)
    kullanmak serbest mi?
13. **Pratik çıktıların eşleşmesi:** imaj pratik girdilerde yeniden çalıştırıldığında commit'li çıktılarla
    "eşleşmeli". LLM kullanan bir pipeline birebir aynı dosyayı üretmez; tolerans ne? Elle yazılmış pratik
    çıktılar commit'lemek (leaderboard için) kural ihlali sayılır mı?
14. **Gizli set:** 9 parça, 3/3/3 kademe dağılımı ve "farklı bir montajdan" bilgisi doğru mu? `facts.json` aynı
    alanlarla (delik merkezleri dahil) gelecek mi? `context.png` olacak mı?
15. **Final sıralaması:** yalnızca gizli skor mu belirliyor, yoksa leaderboard'daki pratik skorun da payı var mı?
    İlk 4'ten sonra kazananı jüri mi seçiyor (skor mu, sunum mu)?

## B. Yöntem: kendi açık sorularımız

Önerilen yön **hibrit**: geometrinin iskeleti deterministik (görünüm siluetleri + `facts.json`), LLM yalnızca
brief'teki sayıları yapılandırılmış özellik listesine çevirir, her özellik ayrı ayrı doğrulanır.

Bugünkü durum (`AGENT_CARVE_MODE=features`, `src/agentic_cad/features.py`):
model JSON döner → `compile_script` bunu `cadlib` çağrılarından oluşan bir betiğe çevirir → `runner.py` betiği
çalıştırıp STEP yazar. Model hiç kod yazmaz. Şema yalnızca **malzeme çıkaran** altı özellik içerir (box,
cylinder, cone, ring, prism, revolve) ve siluet gövdesinin üstüne uygulanır.

1. JSON şemasına **malzeme ekleyen** özellikler eklenirse (aynı altı tip + `"op": "add"`), basit parçalar
   siluet gövdesi olmadan, doğrudan JSON'dan gerçek silindir/düzlem yüzeylerle kurulabilir. Basit kademede
   bu, çokgen yüzeyli gövdeden daha yüksek yüzey puanı verir mi?
2. Hangi parçada hangi yol: tam JSON modeli mi, siluet gövdesi + JSON kesimleri mi? Seçim ölçütü ne olmalı
   (yüz sayısı, siluet uyumu, hacim hatası)?
3. Delik sayısı `through_holes_genus` ile tam eşitlenebilir mi? Karmaşık kademede yerel delik puanı 0.707;
   gövdede fazladan açılan ve eksik kalan delikler ayrı ayrı sayılmalı.
4. Siluet gövdesindeki çokgen yaylar gerçek daire/yay ile değiştirilebilir mi (kontura daire oturtma)?
5. Gemma 4 31B FP8 özellik modu pratik sette 0.842 verdi (modelsiz 0.817); 60 parçalık eval setinde modelsiz
   0.825 hacim IoU'suna karşı doğrulanmadı.
6. Yerel skorlayıcı iyimser: zarf bloğuna 0.61 veriyor, slayt 7 sınırlayıcı kutuya 0.32 diyor. Gerçek
   skorlayıcı gelmezse `eval.py` slayt 7'deki tabloya (0.90 / 0.88 / 0.62 / 0.51 / 0.32) oturtulmalı.
7. `artifacts/outputs` boş: elle yazılmış modeller 2026-11-12'de `cad/outputs/` altına taşındı (5, 7, 8'de delik sayıları yanlış: 18/15, 11/10, 25/11).
   Hangi pipeline koşusunun çıktıları gönderim için `artifacts/outputs`'a konacak?
8. `origin/ananas` ve `origin/feat/visual-hull` bu daldan ayrıştı. Kod donmadan (Cuma 08:00) önce hangisi
   `main`'e gidecek?
