import unittest

from src.text_content_generator import Product, validate_content


class ValidateContentTests(unittest.TestCase):
    def setUp(self):
        self.product = Product(
            source_row=2,
            code="MDU3566",
            description="Mô tả sản phẩm nguồn",
        )

    def test_accepts_prompt_specific_structure_and_ending(self):
        content = (
            "MDU3566 – VÁY LỤA TWIST SATIN\n"
            "Thiết kế thanh lịch dành cho nhiều hoàn cảnh.\n"
            "Váy có đầy đủ size dành cho chị yêu từ 40–75kg. "
            "Cam kết sản phẩm đúng mô tả. Hỗ trợ kiểm tra trước khi thanh toán."
        )

        self.assertEqual(validate_content(content, self.product), [])

    def test_rejects_content_without_product_code(self):
        issues = validate_content("Một bài quảng cáo không có mã.", self.product)

        self.assertIn("nội dung phải có mã sản phẩm MDU3566", issues)

    def test_rejects_verbatim_source_description(self):
        product = Product(2, "MDU3566", "MDU3566 mô tả nguyên văn")

        issues = validate_content("MDU3566 mô tả nguyên văn", product)

        self.assertIn("không được sao chép nguyên văn thông tin nguồn", issues)


if __name__ == "__main__":
    unittest.main()

