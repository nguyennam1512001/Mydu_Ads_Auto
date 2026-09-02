# FB Ads Automation

Script tự động tạo **Chiến dịch (Campaign) → Nhóm quảng cáo (Ad Set) → Quảng cáo (Ad)**
trên Facebook Ads Manager bằng Facebook Marketing API chính thức, thay vì tạo tay
từng chiến dịch theo mẫu đặt tên như trong tài khoản (VD: `Adela Shop-...-MDU6507-Nam-26/8-VIDEO-AI`).

Toàn bộ chiến dịch/adset/ad được khai báo trong **1 file YAML**, script sẽ đọc và tạo
tự động qua API. Mặc định mọi thứ tạo ra ở trạng thái **PAUSED** để bạn kiểm tra lại
trước khi bật thật.

---

## 1. Yêu cầu trước khi bắt đầu

- Python 3.9+
- Có quyền **Quản trị viên (Admin)** hoặc ít nhất quyền quảng cáo trên Ad Account
  và trên Fanpage sẽ dùng để đăng quảng cáo
- Có tài khoản **Meta Business Suite / Business Manager**
  (https://business.facebook.com)

---

## 2. Lấy thông tin để gọi được API (làm từ đầu)

Bạn cần 4 thông tin: `FB_APP_ID`, `FB_APP_SECRET`, `FB_ACCESS_TOKEN`, `FB_AD_ACCOUNT_ID`.
Toàn bộ project này chạy qua **GitHub Actions**, nên các giá trị lấy được ở dưới
sẽ được lưu vào **Secrets** của repo (Settings → Secrets and variables → Actions),
không lưu vào file `.env` trên máy.

### Bước 1 — Tạo App trên Meta for Developers
1. Vào https://developers.facebook.com/apps → **Create App**
2. Chọn loại app **Business**
3. Đặt tên app tùy ý (VD: `Adela Ads Automation`) → Create App

### Bước 2 — Thêm sản phẩm Marketing API
1. Trong app vừa tạo, vào **Add Product** → tìm **Marketing API** → Set Up

### Bước 3 — Lấy App ID và App Secret
1. Vào **App Settings → Basic**
2. Copy **App ID** → dán vào `FB_APP_ID`
3. Bấm **Show** cạnh **App Secret** → copy → dán vào `FB_APP_SECRET`

### Bước 4 — Tạo System User để lấy Access Token dài hạn (khuyến nghị)
Cách này cho token **không tự hết hạn** (khác với token lấy từ Graph API Explorer
chỉ sống 1-2 giờ), phù hợp để chạy script lâu dài.

1. Vào **Business Settings** (business.facebook.com/settings) → **Users → System Users**
2. **Add** → đặt tên (VD: `automation-bot`) → chọn vai trò **Admin** → Create System User
3. Bấm **Add Assets**:
   - Ở mục **Ad Accounts**: chọn đúng Ad Account cần dùng (VD: tài khoản đang chứa
     các chiến dịch `Adela Shop-...`) → tick quyền **Manage campaigns**
   - Ở mục **Pages**: chọn Fanpage sẽ dùng để đăng quảng cáo → tick quyền quản lý
4. Bấm **Generate New Token**:
   - Chọn app vừa tạo ở Bước 1
   - Tick các quyền (scope): `ads_management`, `ads_read`, `business_management`,
     `pages_show_list`, `pages_read_engagement`
   - Bấm **Generate Token** → copy token → dán vào `FB_ACCESS_TOKEN`

> Token của System User có thể để **không hết hạn (Never)** khi generate — nên chọn
> tùy chọn đó nếu có, để không phải tạo lại token mỗi vài ngày.

### Bước 5 — Lấy Ad Account ID
Mở Ads Manager, nhìn trên URL sẽ thấy dạng:
```
adsmanager.facebook.com/adsmanager/manage/campaigns?act=887602053973002&...
```
Số sau `act=` chính là Ad Account ID → dán vào `FB_AD_ACCOUNT_ID`
(có ghi `act_` ở đầu hay không đều được, script tự xử lý).

### Bước 6 — App phải ở chế độ Live để chạy thật với tài khoản ngoài
Nếu app đang ở **Development mode**, chỉ các tài khoản có vai trò trong app (admin,
developer, tester) mới gọi được API. Muốn dùng rộng rãi cần **App Review** cho quyền
`ads_management`. Nếu chỉ chạy nội bộ cho chính Business Manager của bạn, thường
**không bắt buộc** phải qua review — kiểm tra thông báo trong App Dashboard nếu gặp lỗi quyền.

---

## 3. Cài đặt dự án

```bash
git clone <repo-cua-ban>
cd fb-ads-automation
```

Project này chạy qua GitHub Actions nên không cần cài Python/thư viện trên máy —
workflow tự cài khi chạy. Việc cần làm ở bước này là khai báo 4 giá trị đã lấy ở
Mục 2 vào **Secrets** của repo:

Repo trên GitHub → **Settings → Secrets and variables → Actions → tab Secrets**
→ **New repository secret**, tạo lần lượt:
- `FB_APP_ID`
- `FB_APP_SECRET`
- `FB_ACCESS_TOKEN`
- `FB_AD_ACCOUNT_ID` (chỉ cần khi chạy chế độ file YAML ở Mục 4-6; chế độ
  Google Sheet ở Mục 4b lấy Ad Account ID riêng theo từng dòng nên không cần)

> Muốn chạy thử trên máy cá nhân (không qua Actions) vẫn được — chỉ cần set các
> biến này thành biến môi trường (environment variable) của shell trước khi chạy
> `python run.py`, không bắt buộc phải tạo file `.env`.

---

## 4. Cấu hình chiến dịch cần tạo

Sao chép file mẫu rồi chỉnh sửa theo dữ liệu thật:

```bash
cp config/campaigns.example.yaml config/campaigns.yaml
```

Mở `config/campaigns.yaml`, mỗi chiến dịch cần khai báo:
- `name`, `objective`, `status`, `special_ad_categories`
- danh sách `adsets`: ngân sách, targeting (đối tượng), tối ưu hóa
- trong mỗi adset là danh sách `ads`, mỗi ad chọn **1 trong 3 cách** tạo nội dung:
  1. `existing_post_id` — dùng lại 1 bài viết đã đăng sẵn trên Fanpage (giống thao
     tác "Sử dụng bài viết hiện có" bạn hay làm khi tạo tay)
  2. `video_path` + `thumbnail_url` — upload video mới từ máy
  3. `image_path` — upload ảnh mới từ máy

Xem đầy đủ ví dụ và comment giải thích trong `config/campaigns.example.yaml`.

**Objective hợp lệ thường dùng:** `OUTCOME_ENGAGEMENT`, `OUTCOME_TRAFFIC`,
`OUTCOME_SALES`, `OUTCOME_LEADS`, `OUTCOME_AWARENESS`, `OUTCOME_APP_PROMOTION`.

**Lưu ý ngân sách:** VND không có phần thập phân, nhập nguyên số tiền
(VD `100000` = 100.000đ/ngày).

---

## 4b. Tạo nhiều campaign cùng lúc từ Google Sheet (thay vì file YAML)

Nếu bạn có 1 Google Sheet liệt kê nhiều campaign cần tạo (mỗi dòng = 1 campaign),
script có thể đọc trực tiếp từ đó thay vì phải sửa tay file YAML mỗi lần.

**Cấu trúc cột đang được script đọc** (đúng theo sheet `Auto_Create_Campaign`):

| Cột | Ý nghĩa | Bắt buộc |
|---|---|---|
| A | ID tài khoản (Ad Account ID) | có |
| B | ID PAGE | có |
| H | Tên Campaign | có |
| I | Ngân sách chiến dịch (VNĐ/ngày, viết `3.000.000 đ` hay `3000000` đều được) | có |
| O | ID POST (bài viết có sẵn trên Page, dùng làm nội dung quảng cáo) | có |
| P | Kết quả — **để trống**, script tự ghi `Thành công - ...` hoặc `Lỗi: ...` sau khi chạy | không (script tự điền) |

Mỗi dòng sẽ tạo ra đúng **1 Campaign → 1 AdSet → 1 Ad**, dùng chung 1 cấu hình
mặc định cho targeting/objective (giống mẫu ở Mục 4, đối tượng Việt Nam 27-55
tuổi, nữ, tối ưu Tin nhắn Messenger). Muốn đổi cấu hình mặc định này, sửa biến
`SHEET_CAMPAIGN_TEMPLATE` trong `src/cli.py`.

Dòng nào cột **Kết quả (P)** đã có giá trị sẽ được **bỏ qua** ở lần chạy sau —
để tránh tạo trùng campaign khi chạy lại script nhiều lần. Muốn chạy lại 1 dòng,
xoá nội dung ô Kết quả của dòng đó rồi chạy script lại.

### Bước 1 — Tạo Service Account (để script tự đọc/ghi sheet, không cần đăng nhập tay)
1. Vào [Google Cloud Console](https://console.cloud.google.com/) → tạo project mới (hoặc dùng project có sẵn)
2. Vào **APIs & Services → Library** → bật **Google Sheets API**
3. Vào **APIs & Services → Credentials** → **Create Credentials → Service Account**
   → đặt tên tuỳ ý → Create
4. Vào Service Account vừa tạo → tab **Keys** → **Add Key → Create new key** →
   chọn **JSON** → tải file JSON key về máy (chỉ cần tạm để lấy nội dung dán vào
   Secret ở Bước 3 bên dưới, xong có thể xoá khỏi máy, không cần giữ trong dự án)
5. Copy **email** của Service Account (dạng `ten-bot@ten-project.iam.gserviceaccount.com`,
   xem trong tab Details)

### Bước 2 — Share Google Sheet cho Service Account
1. Mở Google Sheet của bạn → nút **Share (Chia sẻ)**
2. Dán email Service Account ở Bước 1 vào → chọn quyền **Editor (Người chỉnh sửa)**
   (cần quyền ghi vì script phải ghi kết quả vào cột P) → Send/Share

### Bước 3 — Khai báo Secrets trên GitHub
Repo trên GitHub → **Settings → Secrets and variables → Actions → tab Secrets**
→ **New repository secret**, tạo thêm:
```
GOOGLE_SHEET_ID = 1Wyw1Ot1KNeX5kQWyO9XgRWgjKdNN2hjfEPiELWAiTAc
GOOGLE_SHEET_TAB = Data
GOOGLE_CREDENTIALS = <dán nguyên nội dung file service-account.json>
```
`GOOGLE_SHEET_ID` lấy từ URL sheet, đoạn giữa `/d/` và `/edit`.
`GOOGLE_SHEET_TAB` là tên tab chứa dữ liệu (theo ảnh mẫu là tab `Data`).
`GOOGLE_CREDENTIALS` là **toàn bộ nội dung** file JSON key tải về ở
Bước 1 (mở file bằng Notepad/VSCode, copy hết dán vào Value) — không phải đường
dẫn file, vì Secrets chỉ lưu được text.

### Bước 4 — Chạy
Vào tab **Actions** trên GitHub → chọn workflow **"Tạo Campaign từ Google Sheet"**
→ **Run workflow** → chọn `dry_run = true` để xem thử trước, hoặc `false` để chạy
thật. Workflow (`.github/workflows/run-campaign-from-sheet.yml`) sẽ tự đọc các
Secrets ở trên, ghi `GOOGLE_CREDENTIALS` ra thành file tạm rồi chạy
`python run.py --from-sheet`, xoá file tạm sau khi chạy xong.

Muốn chạy trên máy cá nhân thay vì Actions: set 3 secrets trên thành biến môi
trường của shell (`GOOGLE_SERVICE_ACCOUNT_FILE` trỏ tới đường dẫn file JSON thật
trên máy), rồi chạy:
```bash
python run.py --from-sheet --dry-run
python run.py --from-sheet
```

---

## 5. Chạy thử (dry-run) trước khi tạo thật

Chạy qua GitHub Actions: tab **Actions** → chọn workflow **"Tạo Campaign Facebook Ads"**
→ **Run workflow** → để `dry_run = true`. Hoặc chạy local:
```bash
python run.py config/campaigns.yaml --dry-run
```
Lệnh này chỉ in ra cây Campaign → AdSet → Ad sẽ được tạo, **không gọi API thật**,
giúp bạn kiểm tra file YAML có đúng cấu trúc không trước khi tốn quota API.

## 6. Chạy thật

Chạy qua GitHub Actions: chọn `dry_run = false` khi Run workflow. Hoặc chạy local:
```bash
python run.py config/campaigns.yaml
```
Script sẽ tạo lần lượt từng Campaign, AdSet, Ad và in ra ID tương ứng. Mọi thứ
mặc định ở trạng thái **PAUSED** — vào Ads Manager kiểm tra lại rồi tự bật (Bật/Tắt)
khi đã ưng ý.

---

## 7. Cấu trúc dự án

```
fb-ads-automation/
├── README.md
├── requirements.txt
├── run.py                # điểm chạy chính
├── config/
│   └── campaigns.example.yaml
├── assets/                # để ảnh/video local dùng cho ad ở đây
├── .github/workflows/     # 2 workflow: chạy từ YAML và chạy từ Google Sheet
└── src/
    ├── fb_client.py       # khởi tạo kết nối API
    ├── campaign.py        # tạo Campaign
    ├── adset.py            # tạo Ad Set
    ├── creative.py         # upload ảnh/video + tạo Ad Creative
    ├── ad.py                # tạo Ad
    ├── sheet_client.py      # đọc/ghi Google Sheet (chế độ --from-sheet)
    └── cli.py               # đọc YAML hoặc Google Sheet và chạy toàn bộ pipeline
```

---

## 8. Đẩy dự án này lên GitHub của bạn

Claude không thể tự tạo repo trên tài khoản GitHub của bạn (không có quyền truy cập),
nhưng bạn chỉ cần vài lệnh sau:

```bash
cd fb-ads-automation
git init
git add .
git commit -m "Init: FB Ads automation script"

# Tạo 1 repo trống trên github.com trước (đừng tick "Add README"), sau đó:
git remote add origin https://github.com/<username>/<ten-repo>.git
git branch -M main
git push -u origin main
```

> Nếu muốn, có thể kết nối GitHub trực tiếp trong Claude ở lần trò chuyện sau để
> Claude tạo/commit thẳng vào repo giúp bạn.

---

## 9. Lưu ý quan trọng

- **Chỉ lưu access token / key trong GitHub Secrets**, không ghi thẳng vào code hay
  commit lên repo dưới bất kỳ dạng nào — lộ ra ai cũng chiếm được quyền quản lý
  tài khoản quảng cáo và Google Sheet của bạn.
- Rate limit: Marketing API giới hạn số request/giờ theo tier của app — nếu tạo
  số lượng lớn campaign cùng lúc, nên thêm độ trễ (`time.sleep`) giữa các lần gọi.
- Facebook thường xuyên rà soát để tránh tạo hàng loạt nội dung trùng lặp/spam —
  đảm bảo nội dung/targeting hợp lệ theo chính sách quảng cáo để tránh bị khóa tài khoản.
- Muốn tạo **A/B test** (Thử nghiệm A/B) hoặc **nhân bản (Duplicate)** hàng loạt từ
  1 campaign gốc thay vì khai báo lại từ đầu — có thể mở rộng thêm 1 script dùng
  `campaign.create_copy()` của SDK, nói với Claude nếu cần bổ sung.
