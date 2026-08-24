---
name: temu-listing-automation
description: Automate creating a new product in agentseller.temu.com from a reusable listing.yaml, including visible form fields, options, and local image uploads; use when a Temu Seller Center listing must be prepared from configuration, but stop for human review before final submission.
metadata:
  short-description: 按 listing.yaml 准备 Temu 商品并上传本地图片
---

# Temu 商品上架自动化

使用本技能时，目标是准备好一个可提交的商品草稿，而不是未经确认直接发布。页面文案和步骤可能变化，必须以当前可见页面为准；`listing.yaml` 只是默认值和标签同义词。

## 输入与配置

- 默认配置：`D:\project\temu-listing-ops\config\listing.yaml`。若用户给出其他路径，优先使用用户路径。
- 配置相对路径以项目根目录（通常为 `D:\project\temu-listing-ops`）解析，不以 skill 目录解析；`package_outer` 也可使用配置中明确的绝对路径。
- 先运行 `scripts/validate_listing.py` 检查 YAML、图片路径、数量和品牌/SKU 关系；不要因验证失败而猜测路径。
- 图片配置支持 `images.pack_dir` + `carousel.files`（推荐，保证顺序），或 `source: pack` 且 `files: []`（按目录排序取前 `count` 张）。`package_outer` 必须是单个可读文件。上传前确认所有路径存在、是图片且未超出配置数量。
- 不要把账号、密码、验证码或浏览器存储写入配置文件。

## 浏览器流程

1. 使用 Browser 技能连接 `https://agentseller.temu.com/`。如果未登录，把浏览器交给用户登录；不要读取或代填凭据。遇到 CAPTCHA 必须停下询问用户。
2. 进入“商品管理 → 新建商品”（或当前页面中语义等价的入口）。每次点击、滚动或选择后获取新的 DOM 快照，确认页面状态再继续。
3. 读取 `form_labels`，用标签/占位符/可访问名称的精确匹配优先，数组中的其他文案作为回退；不要依赖脆弱的 CSS 类名或固定坐标。若当前页面没有匹配项，记录实际可见文案并暂停请求用户确认或调整配置。
4. 按页面当前顺序填写：素材语言；轮播图；商品名称；产地级联；材质/属性；规格；包装图片、类型、形状；敏感属性、尺寸、重量。包装图片必须使用 `defaults.images.package_outer` 解析出的规范化绝对路径，并在上传完成后确认页面已出现预览缩略图。轮播图上传后如果平台自动同步商品素材图，只在页面仍要求时操作，不要重复上传。
5. 标题使用 `defaults.title.template`，替换 `{brand}` 和描述值；品牌、厂家 SKU、价格按 `brand_list` 与 `prices` 逐条解析，找不到明确价格时停下，不得用临时价格。
6. 对级联、弹窗、多选、素材中心等自定义组件先读取可见选项，再选择与配置值完全相同的选项；找不到时报告候选项，不要近似选择。素材中心若提示已存在，按页面提示关闭后勾选已有素材并确认。
7. 图片上传属于向 Temu 传输本地文件：第一次上传前说明将上传哪些绝对路径到 agentseller.temu.com，并取得即时确认。用 file chooser 设置文件，不要把路径输入普通文本框。
8. “下一步”只用于完成页面内分步校验。出现必填错误时读取错误文本，修正映射或让用户补充；不要绕过校验。
9. SKU 分类选择“单品”时，若 `defaults.sku.fill_total_contents` 为 `false`，保留页面“共计内含”为空；不要为了消除提示而填入 1 或其他猜测值。
10. 若配置启用 `defaults.images.detail.enabled`，点击“开始装修”，添加与轮播图素材对应数量的“图片”组件；每个组件点“从素材中心添加”，选择 `defaults.images.detail.source` 解析出的轮播素材之一，确认后再添加下一张，最后点击“保存”。详情编辑器中的“保存”不是最终商品创建，不替代最终提交前的人审。
11. 到达 SKU/预览/提交页后，汇总实际填写值、图片文件、价格和未解决字段，停下等待用户确认。只有用户明确确认后才点击最终“提交/立即提交”。

## 配置自适应与回写

页面实际字段优先于旧配置。发现标签、选项值、图片路径或字段结构变化时，先建立“配置键 → 实际页面字段/选项”的映射并展示差异；只有用户同意时才回写 `listing.yaml`，保留原值并以注释或 `overrides` 记录差异；不要删除未知配置项，也不要把一次性文案硬编码成永久规则。

提交后的合规信息（欧代、土代/GPSR 等）属于独立模块：只有配置有明确值且用户另行确认时才进入，不要在新建商品页猜填。

## 安全停止条件

未登录、出现 CAPTCHA/风控、字段无法唯一匹配、图片缺失或格式不符、价格/SKU 不明确、页面要求敏感资料、或最终提交前未获得即时确认时，停止并给出缺口清单。不要通过接口、脚本注入或隐藏字段绕过页面验证。

详细的配置键与解析约定见 [references/listing-schema.md](references/listing-schema.md)。
