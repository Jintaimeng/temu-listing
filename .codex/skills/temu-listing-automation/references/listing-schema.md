# listing.yaml 解析约定

技能读取的配置分为三层：

1. `form_labels`：页面标签的同义词。优先第一个值；页面变化时以当前 DOM 可见文案为准，并报告需要更新的键。
2. `defaults`：一次商品草稿的默认值。多选项按列表全部选择；级联字段按配置顺序逐级打开。
3. `brand_list` + `prices`：品牌和厂家 SKU 的重复商品矩阵。品牌必须出现在 `brand_list`；价格优先匹配同品牌 SKU，其次品牌 default，最后才使用 `defaults.sku.price`，若仍缺失则停止。

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
    package_outer: D:/project/temu-listing-ops/data/packaging/outer.png
```

相对路径以项目根目录解析；`package_outer` 也允许使用规范化绝对路径。Windows 路径大小写通常不敏感，但技能仍应按实际文件存在性校验，并在报告中使用规范化绝对路径。不要上传同一文件两次。若 `files` 为空，按 `pack_dir` 的稳定字典序取 `count` 张，并在提交前展示清单。`detail.source: carousel` 表示详情装修组件复用已解析的轮播素材清单。

SKU 分类为“单品”时，可用 `defaults.sku.fill_total_contents: false` 明确跳过页面中的“共计内含”字段。

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
