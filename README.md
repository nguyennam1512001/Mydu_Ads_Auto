# Mydu Ads Auto

Repository hợp nhất 2 dự án:

- `Auto_Post_Page` → `post_page/`
- `Auto_Create_Campain` → `create_campaign/`

## 🚀 Chạy hệ thống

Vào **Actions → 🚀 Chạy hệ thống → Run workflow** và chọn một trong các chế độ:

1. `Tạo Text Content`
2. `Đăng Facebook Page`
3. `Tạo Campaign`
4. `Tạo Text Content -> Đăng Facebook Page`
5. `Đăng Facebook Page -> Tạo Campaign`
6. `Chạy toàn bộ`

Các chế độ ghép chạy tuần tự. Bước sau chỉ chạy khi bước trước hoàn tất thành công:

- `Tạo Text Content -> Đăng Facebook Page`
- `Đăng Facebook Page -> Tạo Campaign`
- `Chạy toàn bộ`: `Tạo Text Content -> Đăng Facebook Page -> Tạo Campaign`

## Cấu trúc

```text
Mydu_Ads_Auto/
├── post_page/                 # Auto_Post_Page
├── create_campaign/           # Auto_Create_Campain
└── .github/
    └── workflows/
        └── system.yml         # Workflow điều phối
```

## GitHub Secrets cần cấu hình

Các Secret từ 2 repo cũ cần được tạo lại trong repo `Mydu_Ads_Auto`:

- `GOOGLE_SHEET_ID`
- `GOOGLE_SHEET_TAB`
- `GOOGLE_CREDENTIALS`
- `GEMINI_API_KEY`
- `FB_ACCESS_TOKEN`
- `FB_APP_ID`
- `FB_APP_SECRET`
- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_SESSION`

Repository variable tùy chọn:

- `FB_GRAPH_VERSION` — nếu không đặt sẽ dùng `v25.0`.

> GitHub không tự sao chép Secrets khi gộp repository. Bạn cần tạo các Secrets trên trong **Settings → Secrets and variables → Actions** của repo mới.
