# listing.yaml 解析约定

技能读取的配置分为三层：

1. `form_labels`：页面标签的同义词。优先第一个值；页面变化时以当前 DOM 可见文案为准，并报告需要更新的键。
2. `defaults`：一次商品草稿的默认值。多选项按列表全部选择；级联字段按配置顺序逐级打开。
3. `brands`：按顺序配置要分别上架的品牌；每个 `phone_models` 条目必须同时包含 `sku_code`、`declaration_price`、`suggested_retail_price` 和 `suggested_retail_price_currency`。同一 `pack_dir` 会复用到每个品牌草稿；型号匹配大小写不敏感，不允许从厂家 SKU 或默认价格回退。

## 图片

建议使用：
```yaml
defaults:
  images:
    pack_dir: data/image-packs/手机壳一/990f6b43c33d
    carousel:
      count: 5
      source: pack
      files: [detail.png, scene.png, extra1.png, test-phone-case-1.png, test-phone-case-2.png]
    detail:
      enabled: true
      source: carousel
      count: 5
      files: []
    material:
      skip: true
    package_outer: D:/project/temu-listing-ops/data/waibaozhuang-goods.JPEG
```

相对路径以项目根目录解析；`package_outer` 也允许使用规范化绝对路径。Windows 路径大小写通常不敏感，但技能仍应按实际文件存在性校验，并在报告中使用规范化绝对路径。不要上传同一文件两次。若 `files` 为空，按 `pack_dir` 的稳定字典序取 `count` 张，并在提交前展示清单。`detail.source: carousel` 表示详情装修组件复用已解析的轮播素材清单。

SKU 分类为“单品”时，可用 `defaults.sku.fill_total_contents: false` 明确跳过页面中的“共计内含”字段。选择“单品”后仍需点击 SKU 区域的“批量填写”，将分类写入实际组合行。

标题模板保留 `{brand}`，每个品牌创建草稿时替换该占位符。品牌和型号配置示例：

```yaml
brands:
  - brand: Apple
    phone_models:
      - phone_model: iPhone 15
        sku_code: "TEST-APPLE-IPHONE15-001"
        declaration_price: "7.20"
        suggested_retail_price: "16.8"
        suggested_retail_price_currency: CNY
```

这些值必须由用户明确配置，不得相互推算或猜填。品牌字段没有明确配置时保持为空。商品合规声明复选框属于创建前必填确认项，应在最终人工审核前自动勾选，但仍不得代替用户对最终“创建/提交”的即时确认。

## 字段映射

将配置键映射到当前页面可见的 label、placeholder、role/name。对自定义下拉框不要直接写 DOM value：打开选项并选择完全匹配的可见文本。若配置值不在选项中，报告候选项并暂停。

## 可回写变更

页面探索得到的新标签或选项只能在用户同意后回写；推荐追加：
```yaml
overrides:
  observed_at: 2026-08-24
  fields: {}
```
保留旧键，避免破坏其他店铺或商品复用。
