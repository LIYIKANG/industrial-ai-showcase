import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _load_config():
    path = Path(__file__).resolve().parent.parent / "config" / "customer_keywords.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def in_memory_db(monkeypatch):
    import core.database as db_module

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)

    import auth.models  # noqa: F401

    db_module.Base.metadata.create_all(bind=test_engine)
    yield TestSessionLocal
    db_module.Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


def test_parse_comma_format_and_validate():
    from core.customer_keyword_parser import parse_customer_text
    from core.customer_keyword_validator import validate_customer_fields

    config = _load_config()
    parsed = parse_customer_text(
        "LX0145-002-0535-21, NBR, 60±5°A, 黑色, 1.70, 1K",
        config,
    )
    validation = validate_customer_fields(parsed, config)

    assert parsed["input_format"] == "comma"
    assert parsed["normalized"]["tax_included_price"] == 1.70
    assert parsed["normalized"]["moq"] == 1000
    assert validation["is_valid"] is True


def test_parse_key_value_aliases_and_missing_fields():
    from core.customer_keyword_parser import parse_customer_text
    from core.customer_keyword_validator import validate_customer_fields

    config = _load_config()
    parsed = parse_customer_text("SKU: 535-21\n含税价: 1.80", config)
    validation = validate_customer_fields(parsed, config)

    assert parsed["normalized"]["internal_sku"] == "535-21"
    assert parsed["normalized"]["tax_included_price"] == 1.80
    assert validation["is_valid"] is False
    assert "材质及特殊特性" in validation["missing_fields"]


def test_purchase_order_line_items_extract_multiple_prices():
    from core.customer_line_item_parser import extract_customer_line_items_from_text

    config = _load_config()
    text = "\n".join(
        [
            "编号 代码 名称 规格型号 单位 数量 含税单价 价税合计 交货日期 备注",
            "1 ELB000232 垫圈(FOR NW1-35.35R) LX0107-001-0(由CU2473DO设变来) Pcs 1,000 0.456 456.00 2026-7-10",
            "2 ELB000231 Y形垫圈(不用在HC) LX0105-MYP-15-0 Pcs 1,000 2.280 2,280.00 2026-7-10",
            "3 ELB001040 Y形垫圈 LX0145-002-0 Pcs 1,000 1.790 1,790.00 2026-7-10",
        ]
    )

    items = extract_customer_line_items_from_text(text, source="po.pdf", config=config)

    assert [item["tax_included_price"] for item in items] == [0.456, 2.28, 1.79]
    assert [item["quantity"] for item in items] == [1000, 1000, 1000]
    assert [item["prices"][0]["moq"] for item in items] == ["", "", ""]
    assert items[0]["source"] == "po.pdf"
    assert items[2]["part_no"] == "LX0145-002-0"


def test_vlm_line_items_are_normalized():
    from core.customer_line_item_parser import normalize_customer_line_items

    config = _load_config()
    raw_items = [
        {
            "line_no": "1",
            "item_no": "ELB000232",
            "product_name": "垫圈",
            "part_no": "LX0107-001-0",
            "material_special": "NBR",
            "hardness": "50-55°A",
            "color": "黑色",
            "unit": "Pcs",
            "quantity": "1,000",
            "tax_included_price": "0.456",
            "amount": "456.00",
            "lead_time": "2026-7-10",
            "page": 1,
            "source_table": "采购订单明细",
            "confidence": 0.98,
        },
        {
            "line_no": "2",
            "item_no": "ELB000231",
            "product_name": "Y形垫圈",
            "part_no": "LX0105-MYP-15-0",
            "unit": "Pcs",
            "quantity": "1,000",
            "tax_included_price": "2.280",
            "amount": "2,280.00",
            "lead_time": "2026-7-10",
        },
    ]

    items = normalize_customer_line_items(raw_items, source="vlm.pdf", config=config)

    assert [item["tax_included_price"] for item in items] == [0.456, 2.28]
    assert [item["quantity"] for item in items] == [1000, 1000]
    assert [item["prices"][0]["moq"] for item in items] == ["", ""]
    assert items[0]["business_key_id"] == "sku"
    assert items[0]["business_key_value"] == "LX0107-001-0"
    assert items[0]["material_special"] == "NBR"
    assert items[0]["hardness"] == "50-55°A"
    assert items[0]["color"] == "黑色"
    assert items[0]["source_table"] == "采购订单明细"
    assert items[0]["validation_status"] == "ok"


def test_vlm_line_item_keeps_multiple_prices_for_one_sku():
    from core.customer_line_item_parser import normalize_customer_line_items

    config = _load_config()
    raw_items = [
        {
            "sku": "535-10",
            "item_no": "ELB000232",
            "product_name": "垫圈",
            "part_no": "LX0107-001-OS",
            "tax_included_price": "2.28",
            "quantity": "1,000",
            "unit": "Pcs",
            "price_type": "采购订单含税单价",
            "source_table": "采购订单明细",
        },
        {
            "sku": "535-10",
            "item_no": "ELB000232",
            "product_name": "垫圈",
            "part_no": "LX0107-001-OS",
            "material_special": "NBR",
            "hardness": "50-55°A",
            "color": "黑色",
            "prices": [
                {"tax_included_price": "￥0.456", "moq": "1K", "source_table": "产品价格表"},
                {"tax_included_price": "￥0.42", "moq": "10K", "source_table": "产品价格表"},
                {"tax_included_price": "￥0.37", "moq": "20K", "source_table": "产品价格表"},
            ],
        }
    ]

    items = normalize_customer_line_items(raw_items, source="vlm.pdf", config=config)

    assert len(items) == 1
    assert items[0]["sku"] == "535-10"
    assert items[0]["material_special"] == "NBR"
    assert items[0]["hardness"] == "50-55°A"
    assert items[0]["color"] == "黑色"
    assert [price["tax_included_price"] for price in items[0]["prices"]] == [0.456, 0.42, 0.37]
    assert [price["moq"] for price in items[0]["prices"]] == ["1K", "10K", "20K"]
    assert items[0]["tax_included_price"] == 0.456


def test_commit_line_item_creates_with_lowest_price(in_memory_db, tmp_path):
    from core.customer_business_logic import commit_customer_line_item_product
    from core.customer_line_item_parser import normalize_customer_line_items
    from repository.customer_repository import CustomerRepository

    config = _load_config()
    db = in_memory_db()
    try:
        repo = CustomerRepository(db, seed_file=tmp_path / "missing.json")
        line_item = normalize_customer_line_items(
            [
                {
                    "sku": "535-10",
                    "item_no": "ELB000232",
                    "product_name": "垫圈",
                    "part_no": "LX0107-001-OS",
                    "material_special": "NBR",
                    "hardness": "50-55°A",
                    "color": "黑色",
                    "prices": [
                        {"tax_included_price": "0.456", "moq": "1K"},
                        {"tax_included_price": "0.42", "moq": "10K"},
                        {"tax_included_price": "0.37", "moq": "20K"},
                    ],
                }
            ],
            config=config,
        )[0]

        result = commit_customer_line_item_product(line_item, config, repo)
        stored = result["database_product_after"]

        assert result["result_type"] == "created"
        assert result["new_value"] == 0.37
        assert stored["sku"] == "535-10"
        assert stored["tax_included_price"] == 0.37
        assert len(stored["price_tiers"]) == 3
    finally:
        db.close()


def test_commit_line_item_preserves_existing_lower_price(in_memory_db, tmp_path):
    from core.customer_business_logic import commit_customer_line_item_product
    from core.customer_line_item_parser import normalize_customer_line_items
    from repository.customer_repository import CustomerRepository

    config = _load_config()
    db = in_memory_db()
    try:
        repo = CustomerRepository(db, seed_file=tmp_path / "missing.json")
        repo.create_product(
            {
                "sku": "535-10",
                "internal_sku": "535-10",
                "tax_included_price": 0.35,
            },
            "sku",
        )
        line_item = normalize_customer_line_items(
            [
                {
                    "sku": "535-10",
                    "prices": [
                        {"tax_included_price": "0.456"},
                        {"tax_included_price": "0.42"},
                    ],
                }
            ],
            config=config,
        )[0]

        result = commit_customer_line_item_product(line_item, config, repo)
        stored = result["database_product_after"]

        assert result["result_type"] == "no_update"
        assert result["submitted_price"] == 0.42
        assert stored["tax_included_price"] == 0.35
    finally:
        db.close()


def test_commit_line_item_without_price_keeps_price_blank(in_memory_db, tmp_path):
    from core.customer_business_logic import commit_customer_line_item_product
    from core.customer_line_item_parser import normalize_customer_line_items
    from repository.customer_repository import CustomerRepository

    config = _load_config()
    db = in_memory_db()
    try:
        repo = CustomerRepository(db, seed_file=tmp_path / "missing.json")
        line_item = normalize_customer_line_items(
            [{"sku": "NO-PRICE-1", "product_name": "测试品", "prices": []}],
            config=config,
        )[0]

        result = commit_customer_line_item_product(line_item, config, repo)

        assert result["result_type"] == "created"
        assert result["new_value"] is None
        assert result["database_product_after"]["tax_included_price"] == ""
    finally:
        db.close()


def test_batch_response_summarizes_customer_queue(in_memory_db):
    import json

    from api.customer_routes import _batch_response
    from auth.models import ProcessingJob

    db = in_memory_db()
    try:
        jobs = [
            ProcessingJob(
                file_id="job_1",
                username="admin",
                filename="po1.pdf",
                status="done",
                source_type="customer",
                extracted_json=json.dumps(
                    {
                        "batch_id": "batch_test",
                        "original_filename": "po1.pdf",
                        "progress": 100,
                        "line_items_count": 3,
                    }
                ),
            ),
            ProcessingJob(
                file_id="job_2",
                username="admin",
                filename="po2.pdf",
                status="processing",
                source_type="customer",
                extracted_json=json.dumps(
                    {
                        "batch_id": "batch_test",
                        "original_filename": "po2.pdf",
                        "progress": 50,
                    }
                ),
            ),
            ProcessingJob(
                file_id="job_3",
                username="admin",
                filename="po3.pdf",
                status="queued",
                source_type="customer",
                extracted_json=json.dumps(
                    {
                        "batch_id": "batch_test",
                        "original_filename": "po3.pdf",
                        "progress": 0,
                    }
                ),
            ),
        ]
        db.add_all(jobs)
        db.commit()

        response = _batch_response("batch_test", jobs)

        assert response["status"] == "processing"
        assert response["total_files"] == 3
        assert response["done_count"] == 1
        assert response["processing_count"] == 1
        assert response["queued_count"] == 1
        assert response["progress"] == 50
        assert response["jobs"][0]["filename"] == "po1.pdf"
    finally:
        db.close()


def test_customer_product_list_filters_and_sorts(in_memory_db, tmp_path):
    from repository.customer_repository import CustomerRepository

    db = in_memory_db()
    try:
        repo = CustomerRepository(db, seed_file=tmp_path / "missing.json")
        repo.create_product(
            {
                "sku": "SKU-A",
                "item_no": "ELB-A",
                "product_name": "垫圈",
                "material_special": "NBR",
                "hardness": "50-55°A",
                "color": "黑色",
                "price_tiers": [
                    {"tax_included_price": 0.48, "moq": "1K"},
                    {"tax_included_price": 0.37, "moq": "10K"},
                ],
            },
            "sku",
        )
        repo.create_product(
            {
                "sku": "SKU-B",
                "item_no": "ELB-B",
                "material_special": "FKM",
                "color": "黑色",
                "tax_included_price": 1.2,
            },
            "sku",
        )
        repo.create_product(
            {
                "sku": "SKU-C",
                "item_no": "ELB-C",
                "material_special": "NBR",
                "color": "白色",
            },
            "sku",
        )

        result = repo.list_product_rows(
            q="SKU",
            material_special="NBR",
            sort_by="lowest_price",
            sort_dir="desc",
            page=1,
            size=20,
        )

        assert result["total"] == 2
        assert [row["sku"] for row in result["rows"]] == ["SKU-A", "SKU-C"]
        assert result["rows"][0]["lowest_price"] == 0.37
        assert result["rows"][1]["lowest_price"] is None
        assert result["facets"]["materials"] == ["FKM", "NBR"]
    finally:
        db.close()


def test_business_logic_updates_lower_price(in_memory_db, tmp_path):
    from core.customer_business_logic import process_customer_product
    from core.customer_keyword_parser import parse_customer_text
    from core.customer_keyword_validator import validate_customer_fields
    from repository.customer_repository import CustomerRepository

    config = _load_config()
    db = in_memory_db()
    try:
        repo = CustomerRepository(db, seed_file=tmp_path / "missing.json")
        repo.create_product(
            {
                "internal_sku": "LX0145-002-0535-21",
                "material_special": "NBR",
                "hardness": "60±5°A",
                "color": "黑色",
                "tax_included_price": 1.79,
                "moq": 1000,
                "price_tiers": [{"tax_included_price": 1.79, "moq": 1000}],
            },
            "internal_sku",
        )
        parsed = parse_customer_text(
            "LX0145-002-0535-21, NBR, 60±5°A, 黑色, 1.70, 1000",
            config,
        )
        validation = validate_customer_fields(parsed, config)
        result = process_customer_product(parsed, validation, config, repo)

        assert result["result_type"] == "price_updated"
        assert result["old_value"] == 1.79
        assert result["new_value"] == 1.70
        assert result["requires_manual_confirmation"] is False
    finally:
        db.close()


def test_business_logic_keeps_higher_price_for_manual_confirm(in_memory_db, tmp_path):
    from core.customer_business_logic import process_customer_product
    from core.customer_keyword_parser import parse_customer_text
    from core.customer_keyword_validator import validate_customer_fields
    from repository.customer_repository import CustomerRepository

    config = _load_config()
    db = in_memory_db()
    try:
        repo = CustomerRepository(db, seed_file=tmp_path / "missing.json")
        repo.create_product(
            {
                "internal_sku": "LX0107-001-OS",
                "material_special": "NBR",
                "hardness": "50-55°A",
                "color": "黑色",
                "tax_included_price": 0.456,
                "moq": 1000,
            },
            "internal_sku",
        )
        parsed = parse_customer_text(
            "LX0107-001-OS, NBR, 50-55°A, 黑色, 0.50, 1000",
            config,
        )
        validation = validate_customer_fields(parsed, config)
        result = process_customer_product(parsed, validation, config, repo)

        assert result["result_type"] == "no_update"
        assert result["requires_manual_confirmation"] is True
        assert result["old_value"] == 0.456
    finally:
        db.close()
