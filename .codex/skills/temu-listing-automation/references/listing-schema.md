# listing.yaml 解析约定

## 脚本驱动边界

`SKILL.md` 负责流程阶段、依赖关系、停止条件和人工确认；`scripts/validate_listing.py` 与 `scripts/listing_workflow.py` 负责解析配置、生成确定性数据和执行动作。任何浏览器适配器都只消费动作 JSON，不应自行读取旧配置或通过页面视觉顺序推断值。推荐流程如下：

```text
validate_listing.py <config> --json --plan-out <plan.json>
listing_workflow.py <config> --plan <plan.json> --actions-out <actions.json>
listing_workflow.py <config> --plan <plan.json> --state <state.json> --executor "<adapter>"
```

动作执行器须为每个动作返回 `{"ok": true, "evidence": {...}}`；失败时保留状态文件并停止。连接类失败由脚本按默认最多 2 次、90 秒执行超时和递增退避重试，并通过 `probe_action_state` 探测动作是否已经生效；探测结果不明确时不得再次输入。动作状态还会记录尝试次数和耗时，便于定位页面停顿。`await_submission_confirmation` 永远是人工门，脚本不得自动提交。

导航动作的输入/CDP 超时不是立即失败：适配器必须先重新读取当前页和 `user.openTabs()`，检查是否已经出现带 `productDraftId` 的商品发布页或目标表单（如“商品轮播图”）。目标已出现时返回 `already_applied=true` 或 `target_present=true`，脚本会记录恢复成功；只有确认没有目标标签时才允许按动作策略重试，禁止连续点击造成重复草稿。

技能读取的配置分为三层：

1. `form_labels`：页面标签的同义词。优先第一个值；页面变化时以当前 DOM 可见文案为准，并报告需要更新的键。
2. `defaults`：一次商品草稿的默认值。多选项按列表全部选择；级联字段按配置顺序逐级打开。
3. `brands`：按顺序配置要分别上架的品牌/标题组；`brand` 只填写纯品牌名，不拼接型号；同一品牌的型号合并到同一个 `phone_models` 列表。品牌项可用 `title` 保存商品总表中的完整产品标题，并在品牌级 `colors` 配置颜色。每个 `phone_models` 条目只包含 `phone_model`，不预填 `declaration_price`、`suggested_retail_price`、币种、`sku_code`、`color` 或 `craft_code`。顶层 `craft_codes` 是待上架工艺代码列表；每个工艺代码都会与每个品牌组合成独立商品，复用相同图片、品牌和图片材质元数据，Temu 主要材质由 `defaults.attributes.main_material` 独立配置。SKU 行按型号匹配，颜色从当前品牌的 `colors` 读取，工艺从当前任务的 `craft_code` 读取，大小写不敏感，不允许从厂家 SKU 或默认价格回退。同一型号可出现在不同品牌标题组中，但同一标题组内的型号不得重复。
4. `pricing`：报价单同步后的 `quote_rows` 材质编码 × 工艺编码行，仅保留顶层 `craft_codes` 中启用的工艺；`label` 保留报价单实际版本标签（例如苹果/安卓），同一材质/工艺存在多个标签时必须按标签选择，不能只取第一行，也不能用全局数组位置配对价格。`difference` 是全局差价；页面申报价格按匹配行的 `price + difference` 计算，建议零售价按申报价格乘 `suggested_retail_multiplier`（当前为 8）计算。价格只在执行计划/页面填入，不写回品牌型号条目；`source` 记录报价单 URL、工作表和同步日期。
4. `material_codes`：三位字符串编号到图片材质元数据名称的完整字典；只用于运行时解析图片包首图、标题中的 `{material}` 和 SKU 货号，不绑定 Temu 商品属性“主要材质”、品牌或型号。Temu 的主要材质必须单独由 `defaults.attributes.main_material` 配置。
5. `craft_code_names`、`color_codes`：顶层字典保存工艺代码名称和颜色名称映射；顶层 `craft_codes` 保存本次要分别上架的工艺代码列表，品牌通过 `colors` 配置颜色列表，颜色代码由 `color_codes[color]` 运行时查找。
6. `sku_code_rule`：启用后不配置 `sku_code`。图片到位后按 `{material_code}{craft_code}-{phone_model}-{color_code}-{image_id}` 生成 SKU；`image_id` 为首图去掉 `1_` 前缀和扩展名后的图片编码，`material_code` 为图片编码末尾三位。
7. `material_image_rule`：启用后，图片包必须恰好提供 `1_<图片编码>`、`2`、`3`、`4`、`5` 五张图（带图片扩展名），代码按数字组成轮播图；不依赖配置或 manifest 保存图片名称。
8. `attribute_names`：独立配置颜色、工艺、材质的类别名称。标题模板可使用 `{brand}`、`{material}`、`{craft_codes}`、`{desc}`；当 `desc.source: ai` 时，必须在图片包到位后生成或注入 AI 图片特征短语。Temu 主要材质示例配置为 `defaults.attributes.main_material: PC`，与 `material_codes` 独立。

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
  attributes:
    # Temu“主要材质”固定选 PC；不要从 material_codes 回填。
    main_material: PC
```

相对路径以项目根目录解析；`package_outer` 也允许使用规范化绝对路径。Windows 路径大小写通常不敏感，但技能仍应按实际文件存在性校验，并在报告中使用规范化绝对路径。不要上传同一文件两次。图片包的五个文件名无需写入 `listing.yaml`：当 `files` 为空时，按首图 `1_` 和后续纯数字 `2`～`5` 自动发现和排序。`detail.source: carousel` 表示详情装修组件复用已解析的轮播素材清单。

运行 `scripts/validate_listing.py <配置路径> --json` 会生成图片包级执行计划，其中：

- `material_cache_key` 是按轮播文件顺序和文件内容生成的 SHA-256 指纹；素材复用状态还必须以当前店铺/Chrome 配置隔离。
- `material_search_terms` 是后续品牌在素材中心精确查找的文件名；命中时只选择，不再上传。
- `brands` 是已按配置顺序分组的品牌输入；校验脚本输出的执行计划会进一步展开为“品牌 × 颜色 × craft_code”商品 payload，每个 payload 只包含一个 `color`/单元素 `colors`，因此同一品牌的不同颜色必须分别建立商品草稿。每项包含最终标题及其全部手机型号；`pricing` 携带报价表、差价、倍率和币种。图片包首图材质编码就绪后，以当前 `craft_code` 查表并生成两种页面价格；不得从手机型号条目或旧价格回退。每项同时输出 `spec_counts`：`phone_model_count`、`color_count`、`spec_value_count`（型号数+当前颜色数）以及 `sku_combination_count`（型号数×当前颜色数）。前者用于商品规格数量核对，后者用于接受 Temu SKU 信息表自动生成的组合行。
- `detail_files` 当前按 `detail.source: carousel` 复用轮播顺序；它表示绑定顺序，不表示重新上传。
- 图片包 `1_` 文件是材质编码图；代码必须按 `1` 到 `5` 排序且不读取 `manifest.images`。缺号、重号、后四张带额外编码或首图不带编码都会停止。首图编码解析出的材质只用于标题内部元数据和 SKU；Temu“主要材质”仍固定读取 `defaults.attributes.main_material`。SKU 必须等图片包到位后生成，不能从旧表格或厂家 SKU 回填。

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

pricing:
  source:
    url: "https://docs.qq.com/sheet/DTW14SlpFT3l1eldC?tab=BB08J2"
    sheet_tab: "BB08J2"
    synced_at: "2026-08-27"
    sync_frequency: daily
  difference: 0
  suggested_retail_multiplier: 8
  declaration_price_currency: store_currency
  suggested_retail_price_currency: USD
  quote_rows:
    - material_code: "007"
      craft_code: "GY"
      price: 3.8
```

`difference` 必须由用户明确配置；报价由同步脚本读取，不手工猜填。`declaration_price = quote_rows.price + difference`，`suggested_retail_price = declaration_price * suggested_retail_multiplier`。品牌字段没有明确配置时保持为空。商品合规声明复选框属于创建前必填确认项，应在最终人工审核前自动勾选，但仍不得代替用户对最终“创建/提交”的即时确认。

## 字段映射

将配置键映射到当前页面可见的 label、placeholder、role/name。对自定义下拉框不要直接写 DOM value：打开选项并选择完全匹配的可见文本。颜色字段是例外：允许直接输入配置颜色，不要求候选列表一定包含该值，但必须读取输入框最终值并确认规格表实际新增该颜色行；其他字段若配置值不在选项中，报告候选项并暂停。

## 可回写变更

页面探索得到的新标签或选项只能在用户同意后回写；推荐追加：
```yaml
overrides:
  observed_at: 2026-08-24
  fields: {}
```
保留旧键，避免破坏其他店铺或商品复用。
