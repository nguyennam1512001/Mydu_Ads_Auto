# 13. Tạo Campaign Doanh số Website từ FB_UPLOAD_ID

Chạy thủ công workflow `13. Tạo Campaign Doanh số Website từ FB_UPLOAD_ID` trong GitHub Actions. `limit` giới hạn số dòng hợp lệ cần xử lý; để trống để xử lý tất cả. Dùng các secret Facebook và Google hiện có, không cần secret Telegram.

## Dữ liệu

- Ghép `Lên Camp` và `Bài viết` theo **cùng số hàng**, giống workflow 06. Không kiểm tra hoặc đối chiếu Mã; Mã trùng được phép.
- `Lên Camp`: `AD_ACCOUNT_ID`, `PAGE_ID`, `CAMPAIGN_NAME`, `Mã`, `DAILY_BUDGET`, `SCHEDULE_DATE`, `SCHEDULE_TIME`, `URL_Ladi`, `Pixel`, `Gender`, `Age`, `Camp_Structure`, `RESULT`.
- `Bài viết`: `FB_UPLOAD_ID`, `Text_Content`, `Title`; `Image Hash` là tùy chọn. `POST_ID` và `Post Link` dùng để ghi kết quả.
- Bỏ qua hàng đã có `RESULT`. Hàng thiếu dữ liệu được ghi lỗi; không tạo quảng cáo cho hàng đó. Muốn thử lại, sửa dữ liệu rồi xóa `RESULT` của đúng hàng.
- Lấy Page từ `PAGE_ID` trong Lên Camp, giống workflow 06. `FB_UPLOAD_ID` phải là ID video dạng số, không phải POST_ID hoặc đường dẫn.

## Hành vi

Dùng video có sẵn, không tải Telegram, không upload lại và không ghi đè `FB_UPLOAD_ID`. Ưu tiên `Image Hash` trần hoặc dạng `AD_ACCOUNT_ID:hash` đúng tài khoản. Hash trống, `ko`, hoặc thuộc tài khoản khác sẽ dùng thumbnail từ video Facebook (cơ chế chờ hiện có, tối đa 600 giây).

Giữ cấu hình workflow 06: `OUTCOME_SALES`, ngân sách campaign, `OFFSITE_CONVERSIONS`, Pixel sự kiện `PURCHASE`, đích `WEBSITE`, nút `ORDER_NOW` trỏ đến `URL_Ladi`, lịch chạy, tuổi, giới tính và cấu trúc campaign/adset/ad từ sheet. Campaign, adset và ad được tạo ở trạng thái **ACTIVE** như workflow 06.

Kết quả ghi vào `RESULT` trong Lên Camp; `POST_ID` và `Post Link` mới ghi vào cùng hàng trong Bài viết. Workflow chỉ chạy khi được bấm chạy thủ công; việc thêm file không tự tạo quảng cáo.

Workflow 06 tiếp tục dùng nguồn Telegram mặc định. Workflow 12 giữ nguyên.

