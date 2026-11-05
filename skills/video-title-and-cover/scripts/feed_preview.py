#!/usr/bin/env python3
"""把封面放进 YouTube 和小红书信息流里实际显示的大小，出一张对比图，并算出字在那里有多大。

用法：
  python3 feed_preview.py 第三版=old-16x9.png 第四版=new-16x9.png 第四版=new-3x4.png \\
      --title "YouTube 标题" --xhs-title "小红书标题" -o 工程/封面/feed-preview.png
  python3 feed_preview.py new-16x9.png --ink 16,24   # 再算这几行字高在各处约合几号字（3:4 用 --ink-34）

- 按宽高比自动归类：16:9 放进 YouTube，3:4 放进小红书，别的比例跳过。
- 写成“名字=路径”可以给每一版起名，方便新旧版并排比。
- 对比图按 2 倍像素画，在 Retina 屏上约等于实际显示大小。旁边画同样大小的标题字，
  一眼就能看出封面上的字比界面上的标题大还是小。
- --ink（16:9）和 --ink-34（3:4）填每行字的字高占画面高度的百分比（排版脚本通常会打印），
  会算出在每个位置约合几号字、是旁边标题字的几倍。
- 只需要 Pillow。中文字体默认找苹方，找不到就用 --font 指定。

判断标准见 ../references/cover-production.md。平台改版后重新量，更新下面的尺寸常量。
"""
import argparse
import glob
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

K = 2  # 每个 CSS 像素（或 iOS 点）画成 2 个像素

# 2026-11-04 在桌面 Chrome 实测（窗口约 1367 像素宽）。
YT_RAILS = [(168, 94), (202, 114)]  # 播放页右侧推荐栏：窗口窄时 168×94，常见 202×114
YT_RAIL_COLUMN = 402                # 推荐栏一整列约 402 宽，封面右边剩下的是标题区
YT_RAIL_TITLE = 14                  # 推荐栏标题 14 号字，中等粗细
YT_HOME = (400, 225)                # 首页卡片
YT_HOME_TITLE = 16                  # 首页标题 16 号字
# 小红书没有实测：手机双列每张卡约占屏宽一半，按 393 点宽的 iPhone 估算。
XHS_CARD = 190
XHS_TITLE = 14
GRID3_CELL = 128                    # 三列作品网格，同样按屏宽估算

CJK_INK = 0.92  # 汉字字形的实际高度约为字号的 0.92 倍，用来把“字高”换算成“几号字”

LIGHT = dict(bg=(255, 255, 255), ink=(15, 15, 15), meta=(96, 96, 96), panel=(242, 242, 242),
             card=(255, 255, 255), label=(204, 0, 0), avatar=(210, 210, 210))
DARK = dict(bg=(15, 15, 15), ink=(241, 241, 241), meta=(170, 170, 170), panel=(30, 30, 30),
            card=(36, 36, 36), label=(255, 120, 120), avatar=(70, 70, 70))

FONT_FILES = [
    '/System/Library/AssetsV2/com_apple_MobileAsset_Font*/*/AssetData/PingFang.ttc',
    '/System/Library/Fonts/PingFang.ttc',
    '/System/Library/Fonts/Hiragino Sans GB.ttc',
    '/System/Library/Fonts/STHeiti Medium.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
]
STYLES = {'regular': ('Regular', 'W3', 'Medium'),
          'medium': ('Medium', 'W6', 'Regular', 'W3'),
          'bold': ('Semibold', 'Bold', 'W6', 'Medium')}


class Fonts:
    def __init__(self, path=None):
        self.cache = {}
        if path:
            self.path, self.index = path, {k: 0 for k in STYLES}
            return
        for pattern in FONT_FILES:
            for p in sorted(glob.glob(pattern)):
                index = self._faces(p)
                if index:
                    self.path, self.index = p, index
                    return
        sys.exit('找不到中文字体，请用 --font 指定一个 .ttf/.otf/.ttc 文件')

    @staticmethod
    def _faces(path):
        """在字体集里挑简体中文那一族，返回 {常规/中等/粗体: index}。"""
        families = {}
        for i in range(64):
            try:
                family, style = ImageFont.truetype(path, 12, index=i).getname()
            except OSError:
                break
            if not family.startswith('.'):
                families.setdefault(family, {}).setdefault(style, i)
        if not families:
            return None
        pick = next((f for f in families if ' SC' in f or 'GB' in f), next(iter(families)))
        styles = families[pick]
        return {key: next((styles[n] for n in names if n in styles), next(iter(styles.values())))
                for key, names in STYLES.items()}

    def get(self, size, weight='regular'):
        key = (size, weight)
        if key not in self.cache:
            self.cache[key] = ImageFont.truetype(self.path, int(size * K), index=self.index[weight])
        return self.cache[key]


def classify(img):
    r = img.width / img.height
    if abs(r - 16 / 9) / (16 / 9) < 0.03:
        return '169'
    if abs(r - 3 / 4) / (3 / 4) < 0.03:
        return '34'
    return None


def wrap(draw, text, font, width, max_lines):
    lines, cur = [], ''
    for ch in text:
        if cur and draw.textlength(cur + ch, font=font) > width:
            lines.append(cur)
            cur = ch.lstrip()
        else:
            cur += ch
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        last = lines[max_lines - 1]
        while last and draw.textlength(last + '…', font=font) > width:
            last = last[:-1]
        lines = lines[:max_lines - 1] + [last + '…']
    return lines


def paste_rounded(canvas, img, x, y, w, h, radius):
    thumb = img.resize((w * K, h * K), Image.LANCZOS)
    mask = Image.new('L', thumb.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, thumb.width - 1, thumb.height - 1), radius * K, fill=255)
    canvas.paste(thumb, (x, y), mask)


def overlay(canvas, box, radius, rgba):
    """在 box 范围内叠一块半透明圆角色块（只合成这一小块）。"""
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    layer = Image.new('RGBA', (x1 - x0, y1 - y0), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((0, 0, x1 - x0 - 1, y1 - y0 - 1), radius, fill=rgba)
    canvas.alpha_composite(layer, dest=(x0, y0))


def duration_badge(canvas, fonts, x, y, w, h, text, margin):
    f = fonts.get(12, 'medium')
    d = ImageDraw.Draw(canvas)
    tw = d.textlength(text, font=f)
    bw, bh = tw + 8 * K, 18 * K
    x1, y1 = x + w * K - margin * K, y + h * K - margin * K
    overlay(canvas, (x1 - bw, y1 - bh, x1, y1), 4 * K, (0, 0, 0, 205))
    d.text((x1 - bw / 2, y1 - bh / 2), text, font=f, fill=(255, 255, 255), anchor='mm')


def play_icon(canvas, x, y, w):
    r = 9 * K
    cx, cy = x + w * K - 6 * K - r, y + 6 * K + r
    overlay(canvas, (cx - r, cy - r, cx + r, cy + r), r, (0, 0, 0, 90))
    s = 4 * K
    ImageDraw.Draw(canvas).polygon([(cx - s * 0.6, cy - s), (cx - s * 0.6, cy + s), (cx + s, cy)], fill=(255, 255, 255))


def heading(d, fonts, c, y, text, note=None):
    d.text((32 * K, y), text, font=fonts.get(15, 'bold'), fill=c['ink'])
    y += 24 * K
    if note:
        d.text((32 * K, y), note, font=fonts.get(12), fill=c['meta'])
        y += 20 * K
    return y + 8 * K


def label(d, fonts, c, x, y, text):
    d.text((x, y), text, font=fonts.get(12, 'medium'), fill=c['label'])


def draw_rail(canvas, fonts, c, y, items, size, title, channel, duration):
    d = ImageDraw.Draw(canvas)
    tw, th = size
    y = heading(d, fonts, c, y, f'YouTube 播放页右侧推荐栏 {tw}×{th}',
                f'旁边标题 {YT_RAIL_TITLE} 号字（2026-11-04 实测；窗口越窄封面越小）')
    for name, img in items:
        label(d, fonts, c, 32 * K, y + 2 * K, name)
        x = 140 * K
        paste_rounded(canvas, img, x, y, tw, th, 8)
        duration_badge(canvas, fonts, x, y, tw, th, duration, 4)
        tx, ty = x + (tw + 8) * K, y
        width = (YT_RAIL_COLUMN - tw - 8) * K
        for line in wrap(d, title, fonts.get(YT_RAIL_TITLE, 'medium'), width, 2):
            d.text((tx, ty), line, font=fonts.get(YT_RAIL_TITLE, 'medium'), fill=c['ink'])
            ty += 20 * K
        d.text((tx, ty + 4 * K), channel, font=fonts.get(12), fill=c['meta'])
        d.text((tx, ty + 22 * K), '1.2万次观看 · 3天前', font=fonts.get(12), fill=c['meta'])
        y += max(th, (ty - y) // K + 40) * K + 12 * K
    return y + 16 * K


def draw_home(canvas, fonts, c, y, items, title, channel, duration):
    d = ImageDraw.Draw(canvas)
    tw, th = YT_HOME
    y = heading(d, fonts, c, y, f'YouTube 首页 {tw}×{th}', f'标题 {YT_HOME_TITLE} 号字（2026-11-04 实测）')
    x = 140 * K
    bottom = y
    for name, img in items:
        label(d, fonts, c, x, y, name)
        top = y + 20 * K
        paste_rounded(canvas, img, x, top, tw, th, 12)
        duration_badge(canvas, fonts, x, top, tw, th, duration, 8)
        ay = top + (th + 12) * K
        d.ellipse((x, ay, x + 36 * K, ay + 36 * K), fill=c['avatar'])
        tx, ty = x + 48 * K, ay
        for line in wrap(d, title, fonts.get(YT_HOME_TITLE, 'medium'), (tw - 72) * K, 2):
            d.text((tx, ty), line, font=fonts.get(YT_HOME_TITLE, 'medium'), fill=c['ink'])
            ty += 22 * K
        d.text((tx, ty + 4 * K), channel, font=fonts.get(14), fill=c['meta'])
        bottom = max(bottom, ty + 30 * K)
        x += (tw + 16) * K
    return bottom + 24 * K


def draw_xhs(canvas, fonts, c, y, items, title, channel):
    d = ImageDraw.Draw(canvas)
    w = XHS_CARD
    h = round(w * 4 / 3)
    y = heading(d, fonts, c, y, f'小红书手机双列卡片：约 {w} 点宽',
                f'卡片下面的标题约 {XHS_TITLE} 号字（按手机屏宽估算，没有实测）')
    f = fonts.get(XHS_TITLE, 'medium')
    lines = wrap(d, title, f, (w - 16) * K, 2)
    card_h = h + 8 + 20 * len(lines) + 30
    x = 140 * K
    d.rectangle((x - 6 * K, y, x + (len(items) * (w + 6)) * K, y + (26 + card_h + 8) * K), fill=c['panel'])
    for name, img in items:
        top = y + 26 * K
        label(d, fonts, c, x, y + 5 * K, name)
        d.rounded_rectangle((x, top, x + w * K, top + card_h * K), 6 * K, fill=c['card'])
        paste_rounded(canvas, img, x, top, w, h, 6)
        play_icon(canvas, x, top, w)
        tx, ty = x + 8 * K, top + (h + 8) * K
        for line in lines:
            d.text((tx, ty), line, font=f, fill=c['ink'])
            ty += 20 * K
        d.ellipse((tx, ty + 6 * K, tx + 16 * K, ty + 22 * K), fill=c['avatar'])
        d.text((tx + 21 * K, ty + 7 * K), channel, font=fonts.get(11), fill=c['meta'])
        d.text((x + (w - 8) * K, ty + 7 * K), '1.2万', font=fonts.get(11), fill=c['meta'], anchor='ra')
        x += (w + 6) * K
    return y + (26 + card_h + 8 + 24) * K


def draw_grid3(canvas, fonts, c, y, items):
    d = ImageDraw.Draw(canvas)
    w = GRID3_CELL
    h = round(w * 4 / 3)
    y = heading(d, fonts, c, y, f'三列作品网格：约 {w} 点宽', '个人主页、抖音、视频号常见；按手机屏宽估算，没有实测')
    x = 140 * K
    for i in range(max(3, len(items))):
        if i < len(items):
            label(d, fonts, c, x, y, items[i][0])
            paste_rounded(canvas, items[i][1], x, y + 20 * K, w, h, 0)
        else:
            d.rectangle((x, y + 20 * K, x + w * K, y + (20 + h) * K), fill=c['panel'])
        x += (w + 2) * K
    return y + (h + 44) * K


def size_report(contexts):
    print('每行字在信息流里约合几号字（字高 ÷ %.2f）：' % CJK_INK)
    for name, height, title, inks in contexts:
        print(f'- {name}：封面高 {height}，旁边标题 {title} 号字')
        for ink in inks:
            px = ink * height
            size = px / CJK_INK
            print(f'    字高占画面 {ink * 100:g}% → 字高约 {px:.1f}，约合 {size:.0f} 号字，是标题字的 {size / title:.1f} 倍')
    print('判断：每行字都要明显大于旁边的标题字；只确认“读得出”不够。详见 cover-production.md。')


def percents(text):
    """'16,24' 或 '0.16,0.24' -> [0.16, 0.24]。"""
    vals = [float(v) for v in text.split(',') if v.strip()]
    return [v / 100 if v >= 1 else v for v in vals]


def parse_items(specs):
    items = {'169': [], '34': []}
    for spec in specs:
        name, sep, path = spec.partition('=')
        if not sep:
            name, path = Path(spec).stem, spec
        with Image.open(path) as im:
            img = im.convert('RGB')
        kind = classify(img)
        if kind is None:
            print(f'跳过 {path}：{img.width}×{img.height} 不是 16:9 或 3:4', file=sys.stderr)
            continue
        want = (1280, 720) if kind == '169' else (1080, 1440)
        if img.width < want[0]:
            print(f'提醒 {path}：{img.width}×{img.height} 比建议的 {want[0]}×{want[1]} 小', file=sys.stderr)
        items[kind].append((name, img))
    return items


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('images', nargs='+', help='封面图，可写成 名字=路径')
    ap.add_argument('--title', default='视频标题写在这里，最多显示两行，超出的部分会被省略', help='YouTube 标题')
    ap.add_argument('--xhs-title', help='小红书笔记标题，默认同 --title')
    ap.add_argument('--channel', default='课代表立正')
    ap.add_argument('--duration', default='10:49', help='YouTube 右下角的时长标签')
    ap.add_argument('--ink', default='10,15,20,25', help='16:9 封面上的字高占画面高度的百分比，逗号分隔')
    ap.add_argument('--ink-34', default='6,8,10,14', help='3:4 封面上的字高占画面高度的百分比，逗号分隔')
    ap.add_argument('--grid3', action='store_true', help='3:4 封面再放进三列作品网格看一次')
    ap.add_argument('--dark', action='store_true', help='用深色界面')
    ap.add_argument('--font', help='中文字体文件，找不到苹方时用')
    ap.add_argument('-o', '--out', default='feed-preview.png')
    args = ap.parse_args(argv)

    items = parse_items(args.images)
    if not items['169'] and not items['34']:
        sys.exit('没有可用的 16:9 或 3:4 封面')
    fonts = Fonts(args.font)
    c = DARK if args.dark else LIGHT
    xhs_title = args.xhs_title or args.title

    width = 140 + max(YT_RAIL_COLUMN, len(items['169']) * (YT_HOME[0] + 16),
                      len(items['34']) * (XHS_CARD + 6), 3 * (GRID3_CELL + 2)) + 40
    n169, n34 = len(items['169']), len(items['34'])
    height = 120 + 2 * (60 + n169 * 170) + (n169 > 0) * 430 + (n34 > 0) * 470 + args.grid3 * 260
    canvas = Image.new('RGBA', (width * K, height * K), c['bg'] + (255,))
    d = ImageDraw.Draw(canvas)
    d.text((32 * K, 28 * K), '封面在信息流里的实际大小', font=fonts.get(18, 'bold'), fill=c['ink'])
    d.text((32 * K, 54 * K), '按 2 倍像素画，在 Retina 屏上约等于实际显示大小；封面上的字要比旁边的标题字明显大',
           font=fonts.get(12), fill=c['meta'])
    y = 92 * K
    contexts = []
    ink169, ink34 = percents(args.ink), percents(args.ink_34)
    if items['169']:
        for size in YT_RAILS:
            y = draw_rail(canvas, fonts, c, y, items['169'], size, args.title, args.channel, args.duration)
            contexts.append((f'YouTube 推荐栏 {size[0]}×{size[1]}', size[1], YT_RAIL_TITLE, ink169))
        y = draw_home(canvas, fonts, c, y, items['169'], args.title, args.channel, args.duration)
        contexts.append((f'YouTube 首页 {YT_HOME[0]}×{YT_HOME[1]}', YT_HOME[1], YT_HOME_TITLE, ink169))
    if items['34']:
        y = draw_xhs(canvas, fonts, c, y, items['34'], xhs_title, args.channel)
        contexts.append((f'小红书双列卡片 {XHS_CARD} 点宽', round(XHS_CARD * 4 / 3), XHS_TITLE, ink34))
        if args.grid3:
            y = draw_grid3(canvas, fonts, c, y, items['34'])
    out = Path(args.out)
    canvas.crop((0, 0, canvas.width, y)).convert('RGB').save(out)
    print(out)
    size_report(contexts)
    return out


if __name__ == '__main__':
    main()
