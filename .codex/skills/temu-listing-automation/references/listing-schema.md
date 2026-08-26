# listing.yaml 解析约定

技能读取的配置分为三层：

1. `form_labels`：页面标签的同义词。优先第一个值；页面变化时以当前 DOM 可见文案为准，并报告需要更新的键。
2. `defaults`：一次商品草稿的默认值。多选项按列表全部选择；级联字段按配置顺序逐级打开。
3. `brands`：按顺序配置要分别上架的品牌/标题组；品牌项可用 `title` 保存商品总表中的完整产品标题，并在品牌级 `colors` 配置颜色。每个 `phone_models` 条目必须同时包含 `phone_model`、`declaration_price`、`suggested_retail_price` 和 `suggested_retail_price_currency`，不预填 `sku_code`、`color` 或 `craft_code`。顶层 `craft_codes` 是待上架工艺代码列表；每个工艺代码都会与每个品牌组合成独立商品，复用相同图片、品牌和材质。SKU 行按型号匹配，颜色从当前品牌的 `colors` 读取，工艺从当前任务的 `craft_code` 读取，大小写不敏感，不允许从厂家 SKU 或默认价格回退。同一型号可出现在不同品牌标题组中，但同一品牌内的型号不得重复。
4. `material_codes`：三位字符串编号到材质名称的完整字典；只用于运行时把图片包首图编号映射为“主要材质”，不绑定品牌或型号。
5. `craft_code_names`、`color_codes`：顶层字典保存工艺代码名称和颜色名称映射；顶层 `craft_codes` 保存本次要分别上架的工艺代码列表，品牌通过 `colors` 配置颜色列表，颜色代码由 `color_codes[color]` 运行时查找。
6. `sku_code_rule`：启用后不配置 `sku_code`。图片到位后按 `{material_code}{craft_code}-{phone_model}-{color_code}-{image_id}` 生成 SKU；`image_id` 为首图去掉 `1_` 前缀和扩展名后的图片编码，`material_code` 为图片编码末尾三位。
7. `material_image_rule`：启用后，图片包必须恰好提供以 `1_`、`2_`、`3_`、`4_`、`5_` 开头的五张图，代码按数字前缀组成轮播图；不依赖配置或 manifest 保存图片名称。
8. `attribute_names`：独立配置颜色、工艺、材质的类别名称。标题模板可使用 `{brand}`、`{material}`、`{craft_codes}`、`{desc}`；当 `desc.source: ai` 时，必须在图片包到位后生成或注入 AI 图片特征短语。

## 图片

建议使用：
```yaml
defaults:
  images:
    pack_dir: data/image-packs/手机壳一/990f6b43c33d
    carousel:
      count: 5
      source: pack
      files: []
    detail:
      enabled: true
      source: carousel
      count: 5
      files: []
    material:
      skip: true
    package_outer: D:/project/temu-listing-ops/data/waibaozhuang-goods.JPEG
```

相对路径以项目根目录解析；`package_outer` 也允许使用规范化绝对路径。Windows 路径大小写通常不敏感，但技能仍应按实际文件存在性校验，并在报告中使用规范化绝对路径。不要上传同一文件两次。图片包的五个文件名无需写入 `listing.yaml`：当 `files` 为空时，按 `1_` 到 `5_` 数字前缀自动发现和排序。`detail.source: carousel` 表示详情装修组件复用已解析的轮播素材清单。

运行 `scripts/validate_listing.py <配置路径> --json` 会生成图片包级执行计划，其中：

- `material_cache_key` 是按轮播文件顺序和文件内容生成的 SHA-256 指纹；素材复用状态还必须以当前店铺/Chrome 配置隔离。
- `material_search_terms` 是后续品牌在素材中心精确查找的文件名；命中时只选择，不再上传。
- `brands` 是已按配置顺序分组的品牌 payload，每项已经包含最终标题及其全部手机型号、SKU 和价格，不应在逐品牌执行时重新推导。
- `detail_files` 当前按 `detail.source: carousel` 复用轮播顺序；它表示绑定顺序，不表示重新上传。
- 图片包 `1_` 文件是材质编码图；代码必须按 `1_` 到 `5_` 排序且不读取 `manifest.images`。缺号、重号或非数字前缀文件都会停止。SKU 必须等图片包到位后生成，不能从旧表格或厂家 SKU 回填。

执行计划只在本次图片包任务内有效。配置或图片内容变化后必须重新运行校验；`material_cache_key` 改变时旧的素材 ready 状态自动失效。

可选地用 `--plan-out <路径>` 保存执行计划，便于任务中断后恢复。素材缓存记录建议采用以下结构（由执行器按实际页面核验后写入，不由配置猜测）：

```json
{
  "store_id": "store-01",
  "chrome_profile": "store-01",
  "material_cache_key": "<sha256>",
  "status": "ready",
  "files": {
    "detail.png": {"status": "ready", "observed_name": "detail.png", "selected": true},
    "scene.png": {"status": "ready", "observed_name": "scene.png", "selected": true}
  },
  "verified_at": "2026-08-26T00:00:00+08:00"
}
```

只有在当前店铺/Chrome 配置、指纹、文件名搜索结果、已选数量和当前草稿预览均核验通过时，才允许写入 `status: ready`。缓存记录不能代替后续品牌草稿的素材选择、确认和预览验收。

SKU 分类为“单品”时，可用 `defaults.sku.fill_total_contents: false` 明确跳过页面中的“共计内含”字段。选择“单品”后仍需点击 SKU 区域的“批量填写”，将分类写入实际组合行。

标题模板保留 `{brand}`，每个品牌创建草稿时替换该占位符。品牌和型号配置示例：

```yaml
brands:
  - brand: Apple
    title: "适用于Apple手机壳透明防摔保护壳"
    colors:
      - 银色
    phone_models:
      - phone_model: iPhone 15
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
