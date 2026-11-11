# Remote geliştirme: `sphere-gpu` (Brev H100)

Takım olarak tek bir GPU makinesinde geliştiriyoruz. Makinede LLM ve VLM vLLM ile sürekli çalışıyor;
herkes SSH / VS Code ile bağlanıp kendi klasöründe çalışıyor.

| | |
|---|---|
| Makine | `sphere-gpu`: 1× H100 80GB, 16 vCPU, 200GB RAM, 1TB disk (Nebius, Brev üzerinden) |
| Maliyet | ~$5.57/saat açıkken, ~$0.17/saat durdurulmuşken |
| LLM | `Qwen/Qwen3-30B-A3B-FP8` → `http://localhost:8000/v1` (OpenAI uyumlu, tool calling açık) |
| VLM | `Qwen/Qwen2.5-VL-7B-Instruct` → `http://localhost:8001/v1` (OpenAI uyumlu) |

## 1. Erişim (bir kerelik)

1. Brev org davet linkini takımdan al ve tarayıcıda aç (link 7 gün geçerli).
2. GitHub'da repo'ya collaborator olarak eklendiğinden emin ol (repo private).
3. Brev CLI'ı kur ve giriş yap (Windows için [aşağıdaki bölüme](#windows) bak):

   ```sh
   # macOS
   brew install brevdev/homebrew-brev/brev
   # Linux
   bash -c "$(curl -fsSL https://raw.githubusercontent.com/brevdev/brev-cli/main/bin/install-latest.sh)"

   brev login      # org listesinde utkukalkanli.dev-533d83-7s9r seçili olmalı (değilse: brev set)
   brev refresh    # ~/.ssh/config'e sphere-gpu kaydını ekler
   ssh sphere-gpu  # bağlantı testi
   ```

   > **Not:** Org'a katılmak tek başına yetmez. Makine onu oluşturan hesaba ait olduğu için,
   > `brev refresh` başka üyelere kaydı eklemez ve `ssh` / `brev port-forward`
   > "sphere-gpu not found" der. (`brev grant-ssh` işe yaramaz: o yalnızca `brev register` ile
   > kaydedilmiş cihazlar için, `sphere-gpu` ise bir instance.) Çözüm: `brev ssh-key` çıktını
   > (public key) makine sahibine gönder; o VM'deki `~/.ssh/authorized_keys`'e eklesin. Sonra
   > Brev'i aradan çıkarıp doğrudan bağlan: `ssh ubuntu@<VM'in IP'si>`.

4. Push için kendi GitHub SSH anahtarın VM'e iletilir (Brev'in SSH kaydında `ForwardAgent yes`
   zaten var; anahtar VM'e kopyalanmaz). Anahtarın ssh-agent'a yüklü olsun: `ssh-add -l` boşsa
   `ssh-add ~/.ssh/id_ed25519`. VM'de `ssh -T git@github.com` ile test et.

### Windows

Brev CLI Windows'ta sadece **WSL** içinde çalışıyor ve SSH ayarlarını WSL tarafına yazıyor;
Windows'taki VS Code onları göremez. Bu yüzden ek bir adım var:

1. PowerShell'i **yönetici olarak** aç, WSL'i kur, bilgisayarı yeniden başlat:

   ```powershell
   wsl --install -d Ubuntu-22.04
   ```

2. Başlat menüsünden **Ubuntu**'yu aç ve Brev'i orada kur:

   ```sh
   bash -c "$(curl -fsSL https://raw.githubusercontent.com/brevdev/brev-cli/main/bin/install-latest.sh)"
   brev login
   brev refresh
   ```

3. Brev'in SSH kaydını Windows tarafına kopyala (Ubuntu içinde):

   ```sh
   curl -fsSL -o brev-windows-ssh.sh https://raw.githubusercontent.com/utkukalkanli/sphere-hackathon/feat/llm-cad-agent/scripts/brev-windows-ssh.sh
   ```

   Repo private olduğu için bu link çalışmazsa, script'i GitHub web arayüzünden
   (`scripts/brev-windows-ssh.sh`) kopyalayıp Ubuntu'da aynı isimle kaydet. Sonra:

   ```sh
   bash brev-windows-ssh.sh
   ```

   Script `C:\Users\<sen>\.ssh\config` dosyasına `sphere-gpu` kaydını ve anahtarı yazar.
   **Makine her `brev start` sonrası** SSH portu değişebilir: o zaman Ubuntu'da
   `brev refresh && bash brev-windows-ssh.sh` komutunu tekrar çalıştır.

4. PowerShell'de test et: `ssh sphere-gpu`. Sonra VS Code adımlarına geç (VS Code'u Windows'a
   normal şekilde kur, WSL içine değil).

5. GitHub push için (opsiyonel): Windows ssh-agent'ı aç ve GitHub anahtarını ekle
   (PowerShell, yönetici):

   ```powershell
   Get-Service ssh-agent | Set-Service -StartupType Automatic; Start-Service ssh-agent
   ssh-add $env:USERPROFILE\.ssh\id_ed25519
   ```

## 2. VS Code

1. **Remote - SSH** eklentisini kur (`ms-vscode-remote.remote-ssh`).
2. `Cmd/Ctrl+Shift+P` → **Remote-SSH: Connect to Host** → `sphere-gpu`
   (platform sorarsa **Linux**; ilk bağlantı ~1 dk sürer).
3. **File → Open Folder** → kendi klasörün (`~/sphere-<isim>`, aşağıda).

## 3. Kendi klasörünü kur

VM'de herkes aynı Linux kullanıcısı (`ubuntu`) ile giriyor, yani home dizini ortak.
**Başkasının klasöründe çalışma**: branch değiştirmen ya da dosya düzenlemen onu da etkiler.

```sh
git clone git@github.com:utkukalkanli/sphere-hackathon.git ~/sphere-<isim>
cd ~/sphere-<isim>
git config user.name "Ad Soyad"        # global değil, sadece bu repo için
git config user.email "sen@mail.com"
uv sync
```

## 4. Modeller

Modeller herkes için tek kopya çalışıyor; vLLM eşzamanlı istekleri kaldırır.

```sh
~/sphere-hackathon/scripts/serve_models.sh status   # ikisi de UP mı?
```

- `stop` / `restart` herkesin agent'ını durdurur: **önce takıma haber ver.**
- Log izlemek için `tmux attach -t llm` (veya `vlm`), çıkmak için `Ctrl-b d`.
  İçerideyken **`Ctrl-c` yapma**, sunucu durur.
- Qwen3 varsayılan olarak `<think>` bloğu üretir. Kapatmak için isteğe
  `"chat_template_kwargs": {"enable_thinking": false}` ekle.

Örnek istek:

```sh
curl -s localhost:8000/v1/chat/completions -H "Content-Type: application/json" -d '{
  "model": "Qwen/Qwen3-30B-A3B-FP8",
  "messages": [{"role": "user", "content": "Merhaba"}],
  "chat_template_kwargs": {"enable_thinking": false}
}'
```

Laptop'tan erişmek istersen: `brev port-forward sphere-gpu -p 8000:8000`

## 5. tmux

SSH bağlantısı koparsa normal terminalde çalışan her şey ölür; tmux oturumları VM'de yaşamaya devam eder.

| Komut | Ne yapar |
|---|---|
| `tmux new -s <isim>-work` | Kendi adınla yeni oturum aç |
| `tmux ls` | Oturumları listele (`llm`, `vlm` modellere ait, dokunma) |
| `tmux attach -t <isim>-work` | Oturuma geri bağlan |
| `Ctrl-b d` | Oturumdan ayrıl (arkada çalışmaya devam eder) |

## 6. Claude Code

Claude Code VM'de kurulu. `~/.claude` ortak olduğu için herkes kendi config dizinini kullansın,
yoksa ilk giriş yapanın hesabı herkese geçer:

```sh
tmux new -s claude-<isim>
cd ~/sphere-<isim>
CLAUDE_CONFIG_DIR=~/.claude-<isim> claude    # ilk seferde login: URL'i tarayıcıda aç, kodu yapıştır
```

Kalıcı yapmak için `alias claude='CLAUDE_CONFIG_DIR=~/.claude-<isim> claude'` satırını
kendi shell'ine ekleyebilirsin (ortak `~/.bashrc`'ye değil, oturum başında `source` edeceğin bir dosyaya).

## 7. Makineyi açma / kapatma

`brev stop` / `brev start` **herkesi etkiler**, kimin kapatacağını takımla konuşun.

```sh
brev stop sphere-gpu                                     # GPU ücreti durur, disk korunur
brev start sphere-gpu && brev refresh                    # tekrar aç (IP/port değişebilir)
ssh sphere-gpu '~/sphere-hackathon/scripts/serve_models.sh'   # modeller otomatik başlamaz
```
