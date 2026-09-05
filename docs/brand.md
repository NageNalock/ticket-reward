# 视觉设计

本次为现有 macOS 原生工具优化界面，保留登录、运行、日志、配置和菜单栏交互。人物形象按用户反馈采用二次元美少女，作为应用图标与积分面板插画。

## 设计取舍

- 背景 `#F4F7F6`，面板 `#FFFFFF`，正文 `#203D35`。
- 主色 `#23775B`，薄荷底色 `#E4F2EB`，辅助文字 `#63786F`。
- 仅异常状态使用琥珀色或红色，始终同时显示文字状态。
- 字体使用 macOS 系统中文与系统圆体数字，不加载网络字体。
- 使用原生按钮、分段筛选和表格，保留键盘焦点与系统交互。
- 采用固定浅色主题，18 pt 面板圆角，少量清晰分区。
- 主窗口使用普通窗口层级，避免始终遮挡用户的其他工作窗口。

## Logo 资产

`assets/mascot.png` 是透明背景、1254 × 1254 px 的原创人物插画，由内置 imagegen 工具生成。人物为薄荷银色长发、绿色眼睛、白色与深绿服装，手持金色星星票券。

`assets/AppIcon.icns` 通过 macOS 的 `sips` 和 `iconutil` 从源图导出，可运行 `bash scripts/build_icon.sh` 重建。

提交的图片已清理生成记录、EXIF、设备色彩配置及图标工具信息，保留像素、透明度和标准色彩参数。图标构建脚本会自动执行相同的元数据清理。

最终生成提示词：

> Use case: logo-brand. Create one beautiful original anime bishoujo character illustration as the production logo/mascot for a native macOS rewards tracking application. User specifically wants 二次元美少女, elegant Japanese anime girl. Subject: a clearly adult young woman, chest-up portrait, refined beautiful anime face, luminous emerald eyes with delicate highlights, soft mint-silver medium length hair with airy strands and a small dark-green ribbon, gentle confident smile and subtle blush. Clothing: tasteful white blouse with dark evergreen sailor-inspired collar and slim green ribbon, fully clothed. She holds a small pale gold rewards ticket with a crisp five-pointed star near her chest. Style: premium Japanese 2D anime game character art, delicate clean colored line art, sophisticated cel shading and luminous soft highlights, exceptional face and eye design; attractive anime illustration, NOT chibi, NOT childlike, NOT 3D, NOT plush toy, NOT animal, NOT western cartoon. Palette: silver mint, ivory, deep evergreen with subtle gold details. Composition: single centered bust portrait, face is the focus, fills most of a square canvas, complete hair silhouette visible with safe margin, polished compact composition suitable for an app icon, no text, no letters, no watermark, no grid, no mockup. Genuinely transparent background with alpha, no scenery, no circle or square backing.
