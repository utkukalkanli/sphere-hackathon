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
3. Brev CLI'ı kur ve giriş yap:

   ```sh
   # macOS
   brew install brevdev/homebrew-brev/brev
   # Linux
   sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/brevdev/brev-cli/main/bin/install-latest.sh)"

   brev login      # davet ettiğimiz org'u seç
   brev refresh    # ~/.ssh/config'e sphere-gpu kaydını ekler
   ssh sphere-gpu  # bağlantı testi
   ```

4. Push yapabilmek için kendi GitHub SSH anahtarını VM'e iletmen gerekiyor (anahtar VM'e
   kopyalanmaz). `~/.ssh/config` içindeki `Host sphere-gpu` bloğuna şunu ekle:

   ```
   ForwardAgent yes
   ```

   Terminalden bağlanırken `ssh -A sphere-gpu` de aynı işi görür.

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
