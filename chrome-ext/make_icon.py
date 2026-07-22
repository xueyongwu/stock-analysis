"""生成扩展图标 icon{16,32,48,128}.png。改了尺寸/配色重跑一次即可。

Chrome 的 action 图标只吃位图, 不认 SVG, 所以这里用 PIL 画。
造型是一根上行分时线 + 端点圆点, 刻意压在左上~右上: badge 恒在右下角, 那块会被盖住。
16px 下文字("NQ")糊成一坨, 折线还认得出, 故不用文字。
"""
from PIL import Image, ImageDraw

BLUE = (57, 135, 229, 255)  # --blue, 同 etf.html
PTS = [(0.08, 0.78), (0.34, 0.46), (0.52, 0.61), (0.90, 0.15)]
S = 8  # 超采样倍数, PIL 的线段接头有锯齿, 放大画再缩回去就平滑了

for size in (16, 32, 48, 128):
    n = size * S
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    xy = [(x * n, y * n) for x, y in PTS]
    w = int(0.15 * n)
    # 末段方向的单位向量, 箭头照它转
    (x2, y2), (x3, y3) = xy[-2], xy[-1]
    seg = ((x3 - x2) ** 2 + (y3 - y2) ** 2) ** 0.5
    ux, uy = (x3 - x2) / seg, (y3 - y2) / seg
    al, aw = 0.34 * n, 0.21 * n  # 箭头长 / 半宽; 半宽要明显大于 w/2 才看得出是箭头
    # 折线只画到箭头根部稍里面一点, 免得箭头两侧漏出线角
    d.line(xy[:-1] + [(x3 - ux * al * 0.8, y3 - uy * al * 0.8)], fill=BLUE, width=w, joint="curve")
    cx, cy = xy[0]  # 圆头: PIL 没有 linecap, 起点补个圆
    d.ellipse([cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2], fill=BLUE)
    bx, by = x3 - ux * al, y3 - uy * al
    d.polygon([(x3, y3), (bx - uy * aw, by + ux * aw), (bx + uy * aw, by - ux * aw)], fill=BLUE)
    img.resize((size, size), Image.LANCZOS).save(f"chrome-ext/icon{size}.png")
    print(f"icon{size}.png")
