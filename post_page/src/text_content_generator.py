"""Tạo Text_Content từ dữ liệu có sẵn trong tab "Bài viết"."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import gspread
from google import genai
from google.oauth2.service_account import Credentials
from google.genai import errors, types


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
DESTINATION_TAB = "Bài viết"
PROMPT_TAB = "Promt GPT"
PROMPT_NAME_COLUMN = 1  # A
PROMPT_COLUMN = 2  # B
TEMPLATE_COLUMN = 3  # C
DESTINATION_CODE_COLUMN = 4  # D
DESTINATION_DESCRIPTION_COLUMN = 5  # E
DESTINATION_CONTENT_COLUMN = 7  # G
FIRST_DATA_ROW = 2


@dataclass(frozen=True)
class Product:
    source_row: int
    code: str
    description: str


def cell(row: list[str], one_based_column: int) -> str:
    index = one_based_column - 1
    return row[index].strip() if index < len(row) else ""


def open_spreadsheet() -> gspread.Spreadsheet:
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    credentials_json = os.getenv("GOOGLE_CREDENTIALS")
    missing = [name for name, value in [
        ("GOOGLE_SHEET_ID", sheet_id),
        ("GOOGLE_CREDENTIALS", credentials_json),
    ] if not value]
    if missing:
        raise EnvironmentError(f"Thiếu biến môi trường: {', '.join(missing)}")
    try:
        info = json.loads(credentials_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GOOGLE_CREDENTIALS không phải JSON hợp lệ: {exc}") from exc
    credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(credentials).open_by_key(sheet_id)


def read_products(worksheet: gspread.Worksheet) -> list[Product]:
    """Đọc mã ở cột D và thông tin ở cột E của tab Bài viết."""
    values = worksheet.get_all_values()
    products = []
    for row_number in range(FIRST_DATA_ROW, len(values) + 1):
        row = values[row_number - 1]
        code = cell(row, DESTINATION_CODE_COLUMN)
        description = cell(row, DESTINATION_DESCRIPTION_COLUMN)
        if code and description:
            products.append(Product(row_number, code, description))
    return products


def read_prompt_config(
    spreadsheet: gspread.Spreadsheet, prompt_name: str
) -> tuple[str, str]:
    """Tìm Prompt_Name ở cột A và trả về Prompt/Content mẫu cùng hàng."""
    worksheet = spreadsheet.worksheet(PROMPT_TAB)
    values = worksheet.get_all_values()
    wanted = prompt_name.strip().casefold()
    for row_number, row in enumerate(values[1:], start=2):
        name = cell(row, PROMPT_NAME_COLUMN)
        if name.casefold() != wanted:
            continue
        prompt = cell(row, PROMPT_COLUMN)
        template = cell(row, TEMPLATE_COLUMN)
        if not prompt:
            raise ValueError(f"Prompt của '{prompt_name}' tại hàng {row_number} đang trống")
        if not template:
            raise ValueError(f"Content mẫu của '{prompt_name}' tại hàng {row_number} đang trống")
        return prompt, template
    raise ValueError(f"Không tìm thấy Prompt_Name '{prompt_name}' trong tab '{PROMPT_TAB}'")


FINAL_LINE = "SIZE: 40–75kg. Kiểm tra hàng trước khi thanh toán."
FALLBACK_MODELS = ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]
RETRY_DELAYS_SECONDS = [0, 5, 15]
DEFAULT_MAX_WORKERS = 3


def validate_content(content: str, product: Product) -> list[str]:
    issues = []
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if (
        not lines
        or product.code.upper() not in lines[0].upper()
        or "–" not in lines[0]
        or lines[0] != lines[0].upper()
    ):
        issues.append("dòng tiêu đề phải viết hoa theo dạng MÃ SP – TÊN SẢN PHẨM")
    if len(lines) < 5:
        issues.append("phải có tiêu đề, ba đoạn nội dung và dòng kết thúc")
    if not lines or lines[-1] != FINAL_LINE:
        issues.append(f"dòng cuối phải đúng: {FINAL_LINE}")
    if content.strip().casefold() == product.description.strip().casefold():
        issues.append("không được sao chép nguyên văn thông tin nguồn")
    return issues


def request_gemini_with_fallback(
    client: genai.Client, primary_model: str, contents: str
) -> tuple[str, str]:
    """Thử lại lỗi máy chủ và tự chuyển sang model Flash-Lite dự phòng."""
    models = list(dict.fromkeys([primary_model, *FALLBACK_MODELS]))
    last_error: errors.ServerError | None = None
    for model in models:
        for attempt, delay in enumerate(RETRY_DELAYS_SECONDS, start=1):
            if delay:
                print(f"Gemini quá tải; chờ {delay}s rồi thử lại {model}...")
                time.sleep(delay)
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=(
                            "Bạn là người viết quảng cáo thời trang tiếng Việt. "
                            "Chỉ dùng dữ liệu nguồn; không tự thêm chất liệu, màu "
                            "sắc, kiểu dáng hoặc thông số."
                        ),
                        max_output_tokens=700,
                    ),
                )
                if model != primary_model:
                    print(f"Đã chuyển sang model dự phòng {model}.")
                return response.text or "", model
            except errors.ServerError as exc:
                last_error = exc
                print(f"{model} lỗi máy chủ ở lần {attempt}/3: {exc}")
    if last_error:
        raise last_error
    raise RuntimeError("Không có model Gemini khả dụng")


def generate_content(
    client: genai.Client,
    model: str,
    prompt: str,
    template: str,
    product: Product,
) -> str:
    model_input = (
        f"CÂU LỆNH CHÍNH TỪ PROMPT ĐÃ CHỌN:\n{prompt.strip()}\n\n"
        "Dùng nội dung trong MẪU chỉ để học bố cục, giọng văn và cách trình bày. "
        "Không sao chép nguyên văn nội dung mẫu hoặc thông tin sản phẩm.\n\n"
        f"CONTENT MẪU CÙNG PROMPT_NAME:\n{template.strip()}\n\n"
        f"MÃ SP: {product.code}\n"
        f"THÔNG TIN SẢN PHẨM: {product.description}\n\n"
        "Viết một bài quảng cáo mới, khoảng 100 chữ, theo đúng cấu trúc:\n"
        "1. Dòng đầu: MÃ SP – TÊN SẢN PHẨM, viết hoa.\n"
        "2. Đoạn mở đầu giới thiệu điểm nổi bật.\n"
        "3. Đoạn mô tả lại chất liệu, kiểu dáng và chi tiết thiết kế.\n"
        "4. Đoạn gợi ý hoàn cảnh sử dụng.\n"
        f"5. Dòng cuối phải chính xác: {FINAL_LINE}\n"
        "Chỉ trả về Text_Content hoàn chỉnh, không giải thích, không Markdown và "
        "không đặt dấu ngoặc kép ở đầu hoặc cuối."
    )
    issues = []
    for attempt in range(2):
        correction = ""
        if issues:
            correction = "\n\nHãy sửa các lỗi sau: " + "; ".join(issues)
        response_text, _used_model = request_gemini_with_fallback(
            client, model, model_input + correction
        )
        content = re.sub(
            r'^\s*["“”]+|["“”]+\s*$', "", response_text.strip()
        ).strip()
        if not content:
            issues = ["nội dung đang trống"]
            continue
        issues = validate_content(content, product)
        if not issues:
            return content
    raise ValueError(f"Nội dung {product.code} chưa đạt yêu cầu: {'; '.join(issues)}")


def generate_product_content(
    api_key: str,
    model: str,
    prompt: str,
    template: str,
    product: Product,
) -> str:
    """Tạo một bài với client riêng để có thể chạy song song an toàn."""
    print(f"Đang viết {product.code}...")
    with genai.Client(api_key=api_key) as client:
        return generate_content(client, model, prompt, template, product)


def max_workers_for(product_count: int) -> int:
    """Giới hạn số yêu cầu Gemini đồng thời; mặc định 3 để tránh quá tải quota."""
    configured = os.getenv("GEMINI_MAX_WORKERS", str(DEFAULT_MAX_WORKERS))
    try:
        max_workers = int(configured)
    except ValueError as exc:
        raise ValueError("GEMINI_MAX_WORKERS phải là số nguyên") from exc
    return min(product_count, max(1, max_workers))


def run(
    *,
    prompt_name: str,
    dry_run: bool = False,
    overwrite: bool = False,
    limit: int | None = None,
) -> None:
    api_key = os.getenv("GEMINI_API_KEY")
    if not dry_run and not api_key:
        raise EnvironmentError("Thiếu GEMINI_API_KEY")
    spreadsheet = open_spreadsheet()
    prompt, template = read_prompt_config(spreadsheet, prompt_name)
    destination = spreadsheet.worksheet(DESTINATION_TAB)
    products = read_products(destination)
    contents = destination.col_values(DESTINATION_CONTENT_COLUMN)
    pending = []
    for product in products:
        old_content = contents[product.source_row - 1].strip() if product.source_row <= len(contents) else ""
        if not overwrite and old_content:
            continue
        pending.append(product)
    if limit is not None:
        pending = pending[:limit]
    print(
        f"Prompt_Name '{prompt_name}'; tab '{DESTINATION_TAB}': "
        f"{len(products)} sản phẩm hợp lệ; xử lý {len(pending)}."
    )
    if dry_run:
        for product in pending:
            print(f"[DRY-RUN] Bài viết!D{product.source_row}/E{product.source_row} -> G{product.source_row} ({product.code})")
        return
    if not pending:
        print("Hoàn tất: không có Text_Content nào cần tạo.")
        return
    assert api_key is not None
    model = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.5-flash-lite")
    worker_count = max_workers_for(len(pending))
    generated: dict[int, str] = {}
    if worker_count == 1:
        for product in pending:
            generated[product.source_row] = generate_product_content(
                api_key, model, prompt, template, product
            )
    else:
        print(f"Chạy song song tối đa {worker_count} sản phẩm.")
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    generate_product_content,
                    api_key,
                    model,
                    prompt,
                    template,
                    product,
                ): product
                for product in pending
            }
            for future in as_completed(futures):
                product = futures[future]
                generated[product.source_row] = future.result()
    updates = [
        {
            "range": f"G{product.source_row}",
            "values": [[generated[product.source_row]]],
        }
        for product in pending
    ]
    destination.batch_update(updates, value_input_option="RAW")
    print(
        f"Hoàn tất: đã ghi {len(pending)} Text_Content; "
        f"model chính {model}, tối đa {worker_count} luồng, có fallback tự động."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Tạo Text_Content từ Google Sheet")
    parser.add_argument("--prompt-name", required=True, help="Tên trong cột Prompt_Name")
    parser.add_argument("--dry-run", action="store_true", help="Không gọi AI, không ghi Sheet")
    parser.add_argument("--overwrite", action="store_true", help="Ghi đè content đã có")
    parser.add_argument("--limit", type=int, help="Giới hạn số sản phẩm cần xử lý")
    args = parser.parse_args()
    run(
        prompt_name=args.prompt_name,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
