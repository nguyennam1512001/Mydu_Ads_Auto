# Workflow 12 — Tạo Campaign từ FB_UPLOAD_ID

Chọn **12. Tạo Campaign từ FB_UPLOAD_ID** trong GitHub Actions. Workflow dùng
chung cấu hình với workflow 03: OUTCOME_ENGAGEMENT, Messenger,
MESSAGING_PURCHASE_CONVERSION, ngân sách cấp Campaign, đối tượng/lịch chạy,
Camp_Structure và CHAT_TEMPLATE. Campaign, AdSet và Ad đều ACTIVE.

- Tab **Lên Camp**: giữ các cột cấu hình như workflow 03, điền Mã và để RESULT
  trống. POST_ID không bắt buộc và không được dùng.
- Tab **Bài viết**: cần các cột Mã, FB_UPLOAD_ID, Title, Text_Content. Mã phải
  duy nhất; dòng được chọn phải có đủ ba trường video. FB_UPLOAD_ID là một ID
  video dạng số, nên giữ định dạng văn bản để tránh mất chữ số.
- PAGE_ID lấy từ Lên Camp. Token/tài khoản quảng cáo cần quyền sử dụng video đó.
  Workflow tạo creative mới với nút MESSAGE_PAGE và mẫu chào CHAT_TEMPLATE,
  không dùng bài viết hiện có, không tải Telegram hoặc upload lại video.
- Thumbnail được lấy từ Meta, dùng lại khi cùng tài khoản/video trong một lần
  chạy; nếu video còn xử lý thì chờ tối đa 600 giây. Không cần cột Image Hash.
- Kết quả ghi vào RESULT của Lên Camp. Dòng có RESULT được bỏ qua như workflow
  03; workflow 12 không ghi đè dữ liệu trong Bài viết.

Workflow dùng cùng secrets và khóa concurrency như workflow 03. Workflow 03
vẫn dùng POST_ID như trước. Chạy `python -m unittest discover -s tests -p test_video_campaign.py` trong thư mục create_campaign để kiểm tra bằng API giả lập.
