# 测试输入说明

在项目根目录运行：

```bash
python3 main.py < test_inputs/01_existing_equal_price_no_update.txt
```

可以把文件名替换成其他测试文件。

注意：`02_existing_lower_price_update.txt` 和 `04_new_product_create.txt` 会修改本地 JSON 数据库。
如需恢复初始截图样例数据，可用：

```bash
cp mock_db/product_price_database.seed.json mock_db/product_price_database.json
```

