# Channel File Manager Bot

Telegram-ൽ owner മാത്രത്തിന് ഉപയോഗിക്കാവുന്ന channel media-forwarding bot. Bot-ന്റെ UI-ക്കും channel administrator permission പരിശോധിക്കാനും `python-telegram-bot` ഉപയോഗിക്കുന്നു. പഴയ message history വായിക്കാനും media messages forward ചെയ്യാനും configured owner-ന്റെ Telegram account ഉപയോഗിക്കുന്ന `Telethon` വേണം. SQLite ഡാറ്റാബേസ് `/var/data` persistent disk-ൽ സൂക്ഷിക്കുന്നു; Flask health check `/healthz`-ൽ ലഭ്യമാണ്.

## Telegram ചരിത്രം സംബന്ധിച്ച പ്രധാന കുറിപ്പ്

Telegram Bot API ഉപയോഗിച്ച് bot-ന് channel-ന്റെ പഴയ മുഴുവൻ message history സ്വതന്ത്രമായി scan ചെയ്യാൻ സാധിക്കില്ല. അതിനാൽ ഈ implementation പഴയതും പുതിയതുമായ ലഭ്യമായ ചരിത്രം owner-ന്റെ MTProto/Telethon session ഉപയോഗിച്ച് scan ചെയ്യുന്നു. Bot-നെ source-ലും destination-ലും administrator ആക്കിയിട്ടുണ്ടോ എന്നും Bot API വഴി പുതുതായി പരിശോധിക്കും. Destination-ൽ bot-ന് post permission വേണം. Telethon session-ിലുള്ള owner account-ന് source കാണാനും destination-ൽ post ചെയ്യാനും അവകാശം വേണം. Session അക്കൗണ്ടിന്റെ numeric Telegram ID `OWNER_ID`-നോട് ഒത്തിരിക്കണം.

Bot എല്ലാ admin channel-ുകളും സ്വയം കണ്ടെത്തുമെന്ന് കരുതരുത്. Bot administrator ആയി ചേർത്തപ്പോൾ ലഭിക്കുന്ന update വഴി channel register ചെയ്യും; update നഷ്ടമായാൽ channel-ൽ `/register` post ചെയ്യുകയോ സ്വകാര്യ bot chat-ൽ `/addchannel CHAT_ID` ഉപയോഗിക്കുകയോ ചെയ്യാം. ഇവയും bot-ന്റെ administrator നില സ്ഥിരീകരിച്ചശേഷം മാത്രമാണ് channel രജിസ്റ്റർ ചെയ്യുന്നത്.

## സവിശേഷതകൾ

- `/start`-ൽ `▶️ Forward Now`, `📡 Connected Channels`, `ℹ️ Help` എന്നീ inline controls.
- Owner ID-യിലേക്ക് മാത്രം നിയന്ത്രിച്ച UI, callback-ുകളും commands-ുകളും.
- രജിസ്റ്റർ ചെയ്ത channel-ുകളുടെ പേര്, numeric chat ID, bot status, ലഭ്യമായ permission-ുകൾ, pagination.
- Channel-ന്റെ ലഭ്യമായ മുഴുവൻ ചരിത്രവും പഴയതിൽ നിന്ന് പുതിയത് വരെ scan ചെയ്ത് `photo`/`document` media ഫയലുകൾ എണ്ണുന്നു. Protected/no-forward ക്രമീകരണമുള്ള ഉള്ളടക്കം പകർത്തുകയോ download ചെയ്ത് വീണ്ടും upload ചെയ്യുകയോ ഇല്ല; Telegram തന്നെ forwarding തടഞ്ഞാൽ അത് ഒഴിവാക്കും.
- Scan дууссаны дараа eligible тоог харуулж, forwarding-ийг эхлүүлэхийн өмнө эзнээс баталгаажуулна.
- SQLite-д амжилттай forward болсон эх сурвалжийн message ID-г тэмдэглэж, давтан ажиллуулахад аль хэдийн forward хийсэн мессежийг алгасна.
- Batch progress, Telegram `FloodWait` үед хүлээх, task цуцлах, зөвхөн ерөнхий алдааны төрлийг бүртгэх.
- Render health endpoint, ганц polling instance-ийн lock, нэг instance тохиргоо.

## Репозиторийн бүтэц

```text
channel-file-manager-bot/
├── bot.py             # Telegram handlers, scan/forward tasks, Flask health endpoint
├── database.py        # SQLite persistence
├── session_gen.py     # Owner Telethon session үүсгэх local helper
├── requirements.txt
├── render.yaml        # Render Blueprint (Web Service + persistent disk)
├── .env.example       # Нууц утгагүй env загвар
├── .gitignore
└── README.md
```

## Урьдчилсан шаардлага

1. `@BotFather`-аар Telegram bot үүсгээд bot token авна.
2. `my.telegram.org`-оос Telegram API ID болон API Hash авна. Эдгээр нь bot token-оос тусдаа.
3. Bot ажиллуулахыг зөвшөөрсөн Telegram account-даа хамаарах numeric ID болон Telethon session бэлдэнэ.
4. Render account болон GitHub account-аа бэлдэнэ.

## Telethon session бэлтгэх

Session-ийг **итгэмжлэгдсэн local компьютер дээр** үүсгэнэ. Энэ session нь Telegram account-д нэвтрэх эрхтэй маш нууц утга тул source code, GitHub, Telegram чат, issue, screenshot эсвэл нийтэд харагдах log-д бүү оруул.

```bash
cd channel-file-manager-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python session_gen.py
```

Helper нь Telegram-д нэвтрэх код/шаардлагатай нууц үгийг терминал дээр асууна. Дараа нь account-ын numeric ID болон `TELETHON_SESSION` утгыг хэвлэнэ. `TELETHON_SESSION`-г зөвхөн Render-ийн secret environment variable талбарт шууд хуул; `OWNER_ID`-д numeric ID-г тавь. Нийтийн эсвэл бусдын компьютерт session бүү үүсгэ. Хэрэв session ил болсон байж магадгүй бол Telegram-ийн идэвхтэй session-уудаас хүчингүй болгож, шинэ session үүсгэ.

## GitHub болон Render deployment

1. Төслийг **private GitHub repository** болгон push хийнэ. `.env`, session файл, session string болон жинхэнэ credential-уудыг commit хийхгүй.
2. Render dashboard-аас **New → Blueprint** сонгоод энэ repository-г холбоно. Root directory нь repository-ийн төслийн root байна.
3. `render.yaml`-ийн `channel-file-manager-bot` Web Service тохиргоог шалгаад Blueprint-ийг apply хийнэ. Blueprint нь `python bot.py` ажиллуулж, `/healthz`-г health check болгон ашиглана.
   Render-ийн албан ёсны үнийн хуудсанд энэ тохиргооны `0.5c-512mb` service $7/сар, 1 GB persistent disk $0.25/сар гэж байна. Иймээс урьдчилсан суурь өртөг ойролцоогоор $7.25/сар бөгөөд татвар болон нэмэлт хэрэглээ үүнд ороогүй. Render-ийн checkout/payment verification тусад нь шаардаж болно.
4. Render Environment хэсэгт доорх нууц утгуудыг оруулна. YAML дээр `sync: false` гэж тэмдэглэсэн талбаруудад Render утга асууна:
   - `BOT_TOKEN`: BotFather-аас авсан bot token.
   - `OWNER_ID`: зөвшөөрөгдсөн эзний numeric Telegram ID; `session_gen.py`-ийн ID-тэй яг ижил байх ёстой.
   - `TELETHON_API_ID`: `my.telegram.org`-ийн API ID.
   - `TELETHON_API_HASH`: API Hash.
   - `TELETHON_SESSION`: helper-ээс гарсан owner session string.
5. Дараах утгуудыг YAML тохируулсан чигээр нь үлдээнэ: `DATABASE_PATH=/var/data/channelbot.sqlite3`, `BOT_LOCK_PATH=/var/data/channelbot.lock`, `BATCH_SIZE=20`, `PROGRESS_EVERY=20`, `LOG_LEVEL=INFO`.
6. Render-ийн нэг л service instance ажиллаж байгааг шалга. Telegram long polling-д хоёр зэрэг instance ажиллавал polling conflict үүснэ. Blueprint нэг instance болон persistent disk тохируулсан.
7. Deploy амжилттай болсны дараа service URL-ийн `/healthz` руу орж `{"status":"ok"...}` хариуг шалга. Дараа нь bot руу `/start` илгээ.
8. Bot-оо ашиглах channel бүрт administrator болго. Source болон destination-д bot админ байгаа эсэхийг шалгана; destination-д bot-ны post permission мөн шаардлагатай. Шаардлагатай бол `/register` эсвэл `/addchannel -100...` ашиглан channel бүртгэнэ.

### Нууц утгын жишээ (`.env.example`)

`OWNER_ID`, token, API hash болон session-ийн оронд бодит утга бүү бич. `.env.example`-г credential хадгалах зориулалтаар ашиглахгүй.

```dotenv
BOT_TOKEN=
OWNER_ID=
TELETHON_API_ID=
TELETHON_API_HASH=
TELETHON_SESSION=
DATABASE_PATH=/var/data/channelbot.sqlite3
BOT_LOCK_PATH=/var/data/channelbot.lock
BATCH_SIZE=20
PROGRESS_EVERY=20
LOG_LEVEL=INFO
```

## Bot ашиглах

1. Bot-д хувийн чатаар `/start` илгээнэ.
2. `📡 Connected Channels`-оор бүртгэгдсэн channel-ууд болон bot permission/status-ийг шалгана.
3. `▶️ Forward Now` сонгоод source, destination channel сонгоно.
4. Бот owner-ийн Telethon session-ээр бүрэн боломжтой түүхийг scan хийн eligible media-ийн тоо харуулна. Энэ нь олон мянган message-тэй channel-д удаан үргэлжилж болно; явцын мэдээ харагдана.
5. Дэлгэц дээрх тоо, channel-уудыг шалгаад `✅ Confirm and forward` дээр дарж зөвшөөрнө. Зөвшөөрөхгүй бол Cancel сонгоно. Ажиллаж эхэлсний дараа цуцалж болно; өмнө нь амжилттай явуулсан мессежүүд destination-д үлдэнэ.

## Аюулгүй байдал ба хил хязгаар

- Owner-оос бусад хүн command эсвэл inline callback ашиглан forwarding эхлүүлж чадахгүй.
- Bot-ийн admin эрхийг бүртгэх, channel сонгох, баталгаажуулах болон үйлдэл эхлэхийн өмнө дахин шалгана.
- Forward хийх account нь `OWNER_ID`-тэй ижил Telethon owner account байхыг тулгана.
- Зөвхөн эзэн redistributing хийх эрхтэй контентыг сонго. Bot Telegram-ийн permission, privacy, membership эсвэл protected-content тохиргоог тойрч гарахгүй.
- Forward нь Telegram-ийн native forward үйлдэл. Хориглогдсон message-ийг download/re-upload эсвэл өөр аргаар хуулбарлах fallback байхгүй.
- Credentials лог болон Telegram чатад хэвлэгдэхгүй; алдаанд exception class-ийг л бичнэ. Гэсэн ч Render environment-ийг зөвхөн эрхтэй хүн харах боломжтой байлга.
- Forward амжилттай болсон ч яг тэр мөчид процесс унтарвал duplicate тэмдэглэл хадгалагдахаас өмнө дахин оролдох магадлал бий. Бусад тохиолдолд SQLite түүхээр давхардлыг алгасна.
- `SQLite` database болон session persistence нь `/var/data` disk-д хамаарна. Disk-гүй deployment-ийг ашиглавал дахин deploy/restart хийхэд өгөгдөл алдагдаж болно; production-д persistent disk-ийг идэвхтэй байлга.

## Алдааг оношлох

- **`TELETHON_SESSION` тохируулаагүй эсвэл authorization алдаа:** `session_gen.py`-г local дээр дахин ажиллуулж, string-ийг бүтнээр нь Render Environment-д зөв оруул. Log-д session string бүү нэм.
- **Telethon session-ийн ID `OWNER_ID`-тэй таарахгүй:** helper хэвлэсэн ID-г `OWNER_ID` болго, эсвэл зөв Telegram account-аас шинэ session үүсгэ.
- **Channel жагсаалтад алга:** bot-ыг channel admin болгоод channel-д `/register` post хий, эсвэл numeric ID-г мэдэж байвал хувийн чатаар `/addchannel CHAT_ID` явуул.
- **Bot админ биш / destination post permission алдаа:** BotFather-ийн token-оос өөр, Telegram channel-ийн administrator тохиргоог шалгаж bot-д шаардлагатай эрх өг.
- **Telethon source-г уншиж чадахгүй:** owner account тухайн source-д нэвтрэх боломжтой эсэх, зөв session ашиглаж буй эсэхийг шалга.
- **Destination-д бичих эрх алдаа:** owner account өөрөө destination channel owner эсвэл `post_messages` эрхтэй admin байх ёстой. Bot админ гэдэг нь owner account-д бичих эрх олгохгүй.
- **Protected/no-forward:** эдгээр message/channel-ийг бот intentionally алгасна; protection-ийг тойрох тохиргоо байхгүй.
- **`FloodWait`:** Telegram-ын өгсөн хугацааг хүлээгээд task үргэлжилнэ. Их хэмжээний forward хийхэд удаан хүлээлт гарвал cancel хийж, дараа дахин оролд.
- **Polling conflict / `Conflict: terminated by other getUpdates request`:** Render болон өөр компьютер дээр зэрэг ажиллаж буй bot процессыг зогсоо; Render service-ийг ганц instance болго.
- **`/healthz` ажиллахгүй:** Render deploy/build log-ийг шалга. `requirements.txt`, env variables, `PORT` болон service start command-ийг баталгаажуул.
- **Restart/deploy дараа channel database алга:** Render disk зөв `/var/data` mount хийгдсэн эсэх, database path тохирсон эсэхийг шалга.

## Local development

```bash
cp .env.example .env
# .env дээр зөвхөн local untracked орчинд env утгуудаа тохируулна.
set -a; source .env; set +a
python bot.py
```

Local polling болон Render polling-ийг нэг bot token дээр зэрэг ажиллуулахгүй. Python 3.11 ашиглахыг зөвлөж байна.
